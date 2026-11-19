import json
import logging
import re
from pathlib import Path

import pytest

from book_agent.agent import MAX_ITERATIONS, MAX_TURNS
from book_agent.cli import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_VIOLATIONS,
    _print_progress,  # pyright: ignore[reportPrivateUsage]
    agent_command,
    baseline_command,
    format_progress,
    main,
    works_command,
)
from book_agent.domain.models import CatalogBook, Profile
from book_agent.domain.recommendations import Filters, Recommendation
from book_agent.llm.base import LLMResponse, ToolCallPart, Usage
from book_agent.ports import BookSearchError
from book_agent.work_keys import CachedWorkResolver, WorkKeyCache
from tests.fakes import (
    FakeBookSearch,
    FakeLLM,
    FakeWorkResolver,
    text_response,
    tool_call_response,
)
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
    assert output.startswith("Prompt baseline_v2\n")
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
    assert output.startswith("Prompt baseline_v2 | request parser request_parser_v3\n")
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


# --- agent ------------------------------------------------------------------------------


def submission(recommendations: list[Recommendation], call_id: str = "submit-1") -> LLMResponse:
    return tool_call_response(
        ToolCallPart(
            id=call_id,
            name="submit_recommendations",
            arguments={"recommendations": [r.model_dump() for r in recommendations]},
        ),
        usage=Usage(input_tokens=1000, output_tokens=100),
    )


DUNE_AGAIN = rec("Dune", "Frank Herbert", "Like Ficciones.")


async def test_agent_report_for_an_accepted_run_after_a_rejection() -> None:
    llm = FakeLLM([submission([DUNE_AGAIN, *CLEAN_THREE[1:]]), submission(CLEAN_THREE, "s-2")])
    output, code = await agent_command(PROFILE, Filters(max_pages=600), llm=llm, books=catalog())

    assert code == EXIT_OK
    lines = output.splitlines()
    assert lines[0] == "Prompt agent_v3 | feedback submission_feedback_v1"
    assert "Iterations: 2 of 6 | Submissions: 2 (1 rejected)" in lines
    rejection = lines.index("Rejection 1:")
    assert lines[rejection + 1] == (
        "- [already_read] 'Dune' by Frank Herbert: already read (as 'Dune (Dune Chronicles, #1)')"
    )
    assert "1. Hyperion by Dan Simmons" in lines
    assert "Result: PASSED" in lines
    call_line = re.compile(r"^- agent: fake-model \| 1000 in, 100 out \| \d+\.\d{2}s$")
    assert lines[-4] == "LLM calls:"
    assert all(call_line.match(line) for line in lines[-3:-1])
    assert lines[-1] == "Total: 2000 in, 200 out"


async def test_agent_rejections_until_the_limit_exit_with_violations() -> None:
    script = [submission([DUNE_AGAIN, *CLEAN_THREE[1:]], f"s-{i}") for i in range(MAX_TURNS)]
    output, code = await agent_command(PROFILE, Filters(), llm=FakeLLM(script), books=catalog())

    assert code == EXIT_VIOLATIONS
    assert f"Iterations: {MAX_TURNS} of {MAX_ITERATIONS} + 1 repair turn |" in output
    assert f"Rejection {MAX_TURNS}:" in output
    assert "ERROR (iterations_exhausted): no accepted recommendations" in output
    assert "Last submission:" not in output  # it is the last rejection, already shown


async def test_agent_catalog_outage_exits_with_an_error_and_shows_the_lookups() -> None:
    class Down(FakeBookSearch):
        async def search(
            self, query: str, *, language: str | None = None, max_results: int = 10
        ) -> list[CatalogBook]:
            raise BookSearchError("Google Books returned HTTP 503")

    output, code = await agent_command(
        PROFILE, Filters(), llm=FakeLLM([submission(CLEAN_THREE)]), books=Down()
    )
    assert code == EXIT_ERROR
    assert "ERROR (catalog_unavailable): the book catalog could not be reached" in output
    assert "Last submission:" in output
    assert (
        "- [not_found] 'Hyperion' by Dan Simmons: lookup failed (Google Books returned HTTP 503)"
        in output
    )


async def test_agent_failed_interpretation_shows_the_raw_reply() -> None:
    llm = FakeLLM([text_response("Sure!")])
    output, code = await agent_command(PROFILE, Filters(), "algo corto", llm=llm, books=catalog())
    assert code == EXIT_ERROR
    assert "ERROR: Could not interpret the request: no JSON found in the reply" in output
    assert "Iterations:" not in output  # the agent never started


def test_agent_command_accepts_the_baseline_arguments(tmp_path: Path) -> None:
    # Fails on the missing CSV before any network call, after argparse accepted everything.
    argv = ["agent", "una distopía", "--language", "EN", "--max-pages", "300"]
    assert main([*argv, "--csv", str(tmp_path / "x.csv")]) == EXIT_ERROR


async def test_agent_verbose_report_shows_the_trace() -> None:
    search = ToolCallPart(id="s-1", name="search_books", arguments={"query": "foundation"})
    llm = FakeLLM(
        [
            tool_call_response(search, usage=Usage(input_tokens=900, output_tokens=20)),
            submission([DUNE_AGAIN, *CLEAN_THREE[1:]]),
            submission(CLEAN_THREE, "s-2"),
        ]
    )
    output, _ = await agent_command(PROFILE, Filters(), llm=llm, books=catalog(), verbose=True)

    lines = output.splitlines()
    start = lines.index("Trace:")
    assert lines[start + 1 : start + 7] == [
        "Turn 1 (tool_use, 20 out)",
        '  search_books {"query": "foundation"} -> 1 book: Foundation',
        "Turn 2 (tool_use, 100 out)",
        "  submit_recommendations [Dune; The Left Hand of Darkness; Foundation] "
        "-> [error] rejected: book 1 (already_read)",
        "Turn 3 (tool_use, 100 out)",
        "  submit_recommendations [Hyperion; The Left Hand of Darkness; Foundation] -> accepted",
    ]


async def test_agent_report_without_verbose_has_no_trace() -> None:
    output, _ = await agent_command(
        PROFILE, Filters(), llm=FakeLLM([submission(CLEAN_THREE)]), books=catalog()
    )
    assert "Trace:" not in output


def test_verbose_turns_on_debug_logs_for_the_app_only(tmp_path: Path) -> None:
    app = logging.getLogger("book_agent")
    before = app.level
    try:
        main(["agent", "-v", "--csv", str(tmp_path / "x.csv")])
        assert app.level == logging.DEBUG
        main(["agent", "--csv", str(tmp_path / "x.csv")])
        assert app.level == logging.WARNING
    finally:
        app.setLevel(before)


def test_verbose_is_an_agent_option_only() -> None:
    with pytest.raises(SystemExit):
        main(["baseline", "--verbose"])


async def test_agent_verbose_trace_marks_the_notice_and_the_forced_turn() -> None:
    search = ToolCallPart(id="s", name="search_books", arguments={"query": "foundation"})
    script = [tool_call_response(search) for _ in range(MAX_ITERATIONS - 1)]
    output, _ = await agent_command(
        PROFILE,
        Filters(),
        llm=FakeLLM([*script, submission(CLEAN_THREE)]),
        books=catalog(),
        verbose=True,
    )
    assert output.count("  (submit-now notice sent)") == 1
    assert f"Turn {MAX_ITERATIONS} (tool_use, 100 out, forced submit_recommendations)" in output


def test_agent_prompt_option_accepts_the_registered_versions(tmp_path: Path) -> None:
    csv = str(tmp_path / "x.csv")  # fails on the missing CSV, after argparse
    assert main(["agent", "--prompt", "agent_v1", "--csv", csv]) == EXIT_ERROR
    with pytest.raises(SystemExit):
        main(["agent", "--prompt", "agent_v9", "--csv", csv])


async def test_agent_verbose_trace_marks_the_repair_turn() -> None:
    search = ToolCallPart(id="s", name="search_books", arguments={"query": "foundation"})
    script = [
        *(tool_call_response(search) for _ in range(MAX_ITERATIONS - 1)),
        submission([DUNE_AGAIN, *CLEAN_THREE[1:]]),
        submission(CLEAN_THREE, "s-2"),
    ]
    output, code = await agent_command(
        PROFILE, Filters(), llm=FakeLLM(script), books=catalog(), verbose=True
    )
    assert code == EXIT_OK
    assert f"Iterations: {MAX_TURNS} of {MAX_ITERATIONS} + 1 repair turn |" in output
    assert output.count("  (repair turn granted)") == 1


def test_min_pages_flag_is_accepted(tmp_path: Path) -> None:
    # Fails on the missing CSV before any network call, after argparse accepted the flag.
    argv = ["agent", "--min-pages", "300", "--max-pages", "500", "--csv", str(tmp_path / "x.csv")]
    assert main(argv) == EXIT_ERROR


def test_contradictory_page_flags_are_a_clean_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["baseline", "--min-pages", "500", "--max-pages", "300", "--csv", str(tmp_path / "x")]
    assert main(argv) == EXIT_ERROR
    assert "min_pages 500 is above max_pages 300" in capsys.readouterr().out
