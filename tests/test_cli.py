import json
from pathlib import Path

import pytest

from book_agent.cli import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_VIOLATIONS,
    _print_progress,  # pyright: ignore[reportPrivateUsage]
    baseline_command,
    format_progress,
    main,
    works_command,
)
from book_agent.domain.models import Profile
from book_agent.domain.recommendations import Filters, Recommendation
from book_agent.llm.base import Usage
from book_agent.work_keys import CachedWorkResolver, WorkKeyCache
from tests.fakes import FakeBookSearch, FakeLLM, FakeWorkResolver, text_response
from tests.library_data import CLEAN_THREE, PROFILE, catalog, catalog_book, rec
from tests.library_data import book as library_book


def reply(recommendations: list[Recommendation]) -> str:
    return json.dumps({"recommendations": [r.model_dump() for r in recommendations]})


async def command(
    text: str, flags: Filters | None = None, request: str | None = None, *before: str
) -> tuple[str, int]:
    script = [text_response(r, usage=Usage(input_tokens=200, output_tokens=30)) for r in before]
    script.append(text_response(text, usage=Usage(input_tokens=1500, output_tokens=120)))
    return await baseline_command(
        PROFILE, flags or Filters(), request, llm=FakeLLM(script), books=catalog()
    )


async def test_report_for_a_clean_run() -> None:
    output, code = await command(reply(CLEAN_THREE), Filters(max_pages=600))
    assert code == EXIT_OK
    assert output.startswith("Prompt baseline_v1\n")
    assert "Filters: max_pages=600 (from flag)" in output
    assert "Request:" not in output
    assert "1. Hyperion by Dan Simmons" in output
    assert "   Reason: Layered world-building like Dune." in output
    assert "https://books.google.com/books?id=hyperion" in output
    assert "Checks: exists=pass, not in library=pass, max pages=pass, cites history=pass" in output
    assert "Result: PASSED" in output
    assert "LLM calls:\n- recommend: fake-model | 1500 in, 120 out | " in output
    assert "\nValidation: " in output


async def test_report_lists_violations_and_unknowns() -> None:
    books = [
        rec("Dune", "Frank Herbert", "A classic."),
        rec("Solaris", "Stanislaw Lem", "Like Dune."),
    ]
    output, code = await command(reply([*books, CLEAN_THREE[2]]), Filters(max_pages=400))
    assert code == EXIT_VIOLATIONS
    assert "Violations:" in output
    assert "- [already_read] 'Dune' by Frank Herbert: already read" in output
    assert "- [cites_library]" in output
    assert "Unknowns:\n- [max_pages] 'Solaris' by Stanislaw Lem: page count unknown" in output
    assert "Result: FAILED" in output


async def test_passed_with_unknowns_exits_ok() -> None:
    solaris = rec("Solaris", "Stanislaw Lem", "Strange, like Dune.")
    output, code = await command(reply([solaris, *CLEAN_THREE[1:]]), Filters(max_pages=400))
    assert code == EXIT_OK
    assert "Result: PASSED WITH UNKNOWNS" in output


async def test_unparseable_reply_shows_the_raw_text() -> None:
    output, code = await command("Sorry, I can't help with that.")
    assert code == EXIT_ERROR
    assert "ERROR: Unparseable reply: no JSON found" in output
    assert "Raw reply:\nSorry, I can't help with that." in output


def test_missing_csv_is_a_clean_error(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    code = main(["baseline", "--csv", str(tmp_path / "missing.csv")])
    assert code == EXIT_ERROR
    assert capsys.readouterr().out.startswith("error:")


def test_invalid_max_pages_is_rejected_by_argparse() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["baseline", "--max-pages", "many"])
    assert exit_info.value.code == 2


async def test_report_shows_what_was_understood_and_each_call() -> None:
    understood = json.dumps({"preference": "una distopía", "language": "en", "max_pages": 600})
    output, code = await command(
        reply(CLEAN_THREE),
        Filters(max_pages=500),
        "una distopía de menos de 600 páginas en inglés",
        understood,
    )
    assert code == EXIT_OK
    assert output.startswith("Prompt baseline_v1 | request parser request_parser_v1\n")
    assert 'Request: "una distopía de menos de 600 páginas en inglés"' in output
    assert "Preference: una distopía" in output
    assert "Filters: language=en (from text), max_pages=500 (from flag)" in output
    assert "- interpret_request: fake-model | 200 in, 30 out | " in output
    assert "- recommend: fake-model | 1500 in, 120 out | " in output
    assert output.index("Preference:") < output.index("1. Hyperion")


async def test_request_without_filters_says_so() -> None:
    understood = json.dumps({"preference": "something funny"})
    output, _ = await command(reply(CLEAN_THREE), None, "something funny", understood)
    assert "Preference: something funny\nFilters: none" in output


async def test_failed_interpretation_exits_with_an_error() -> None:
    llm = FakeLLM([text_response("I am not sure what you mean.")])
    output, code = await baseline_command(
        PROFILE, Filters(), "under 400 pages", llm=llm, books=catalog()
    )
    assert code == EXIT_ERROR
    assert "ERROR: Could not interpret the request: no JSON found in the reply" in output
    assert "Raw reply:\nI am not sure what you mean." in output
    assert "1. " not in output  # no recommendations


@pytest.mark.parametrize("language", ["English", "eng", "e1"])
def test_invalid_language_flag_is_rejected_by_argparse(
    language: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["baseline", "--language", language])
    assert exit_info.value.code == 2
    assert "use a two-letter ISO 639-1 code" in capsys.readouterr().err


def test_request_text_is_an_optional_positional_argument(tmp_path: Path) -> None:
    # Fails on the missing CSV before any network call, after argparse accepted the text.
    code = main(["baseline", "una distopía", "--language", "EN", "--csv", str(tmp_path / "x.csv")])
    assert code == EXIT_ERROR


# --- works ------------------------------------------------------------------------------

KAFKA_ES, KAFKA_BANTAM, KAFKA_WORK = "9789871165070", "9780553213690", "/works/OL498556W"


async def test_works_command_reports_coverage() -> None:
    profile = Profile(
        name="Me",
        books=[
            library_book("La metamorfosis", "Franz Kafka", isbn13=KAFKA_ES),
            library_book("Unknown edition", "Someone", isbn13=KAFKA_BANTAM),
            library_book("A Kindle book", "Someone"),
        ],
    )
    resolver = CachedWorkResolver(FakeWorkResolver({KAFKA_ES: KAFKA_WORK}), WorkKeyCache())
    output, code = await works_command(profile, resolver)
    assert code == EXIT_OK
    assert output == (
        "Library: 3 books, 2 with an ISBN\n"
        "  1 resolved to a work, 1 unknown to Open Library, 0 not checked yet\n"
        "  1 without an ISBN are covered only by the LLM check"
    )
    assert resolver.changed


async def test_works_command_with_failures_exits_with_an_error() -> None:
    profile = Profile(name="Me", books=[library_book("X", "Y", isbn13=KAFKA_ES)])
    resolver = CachedWorkResolver(FakeWorkResolver(failing={KAFKA_ES}), WorkKeyCache())
    output, code = await works_command(profile, resolver)
    assert code == EXIT_ERROR
    assert "1 lookups failed" in output


async def test_baseline_reports_a_work_key_match() -> None:
    profile = Profile(
        name="Me",
        books=[*PROFILE.books, library_book("La metamorfosis", "Franz Kafka", isbn13=KAFKA_ES)],
    )
    bantam = catalog_book("bantam", "The Metamorphosis", "Franz Kafka", 201, isbn13=KAFKA_BANTAM)
    kafka = rec("The Metamorphosis", "Franz Kafka", "A short classic, like Ficciones.")
    llm = FakeLLM([text_response(reply([kafka, *CLEAN_THREE[1:]]))])  # no judge call scripted
    output, code = await baseline_command(
        profile,
        Filters(),
        llm=llm,
        books=FakeBookSearch([bantam, *catalog().books]),
        works=FakeWorkResolver({KAFKA_ES: KAFKA_WORK, KAFKA_BANTAM: KAFKA_WORK}),
    )
    assert code == EXIT_VIOLATIONS
    assert "(as 'La metamorfosis'; same work key as an edition found)" in output


def test_works_command_fails_cleanly_on_a_missing_csv(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    assert main(["works", "--refresh", "--csv", str(tmp_path / "missing.csv")]) == EXIT_ERROR
    assert capsys.readouterr().out.startswith("error: ")


async def test_works_command_passes_progress_and_checkpoints_through() -> None:
    profile = Profile(name="Me", books=[library_book("X", "Y", isbn13=KAFKA_ES)])
    progress: list[tuple[int, int]] = []
    saves: list[WorkKeyCache] = []
    resolver = CachedWorkResolver(FakeWorkResolver({KAFKA_ES: KAFKA_WORK}), WorkKeyCache())
    await works_command(
        profile, resolver, progress=lambda d, t: progress.append((d, t)), checkpoint=saves.append
    )
    assert progress == [(1, 1)]
    assert saves == []  # 1 lookup is below the checkpoint interval; the caller saves at the end


def test_progress_text() -> None:
    assert format_progress(45, 177) == "Resolving work keys: 45 of 177"


def test_progress_without_a_terminal_prints_every_20_and_the_last(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for done in range(1, 46):
        _print_progress(done, 45)
    assert capsys.readouterr().out.splitlines() == [
        "Resolving work keys: 20 of 45",
        "Resolving work keys: 40 of 45",
        "Resolving work keys: 45 of 45",
    ]
