import json
from pathlib import Path

import pytest

from book_agent.cli import EXIT_ERROR, EXIT_OK, EXIT_VIOLATIONS, baseline_command, main
from book_agent.domain.recommendations import Filters, Recommendation
from book_agent.llm.base import Usage
from tests.fakes import FakeLLM, text_response
from tests.library_data import CLEAN_THREE, PROFILE, catalog, rec


def reply(recommendations: list[Recommendation]) -> str:
    return json.dumps({"recommendations": [r.model_dump() for r in recommendations]})


async def command(text: str, filters: Filters | None = None) -> tuple[str, int]:
    llm = FakeLLM([text_response(text, usage=Usage(input_tokens=1500, output_tokens=120))])
    return await baseline_command(PROFILE, filters or Filters(), llm=llm, books=catalog())


async def test_report_for_a_clean_run() -> None:
    output, code = await command(reply(CLEAN_THREE), Filters(max_pages=600))
    assert code == EXIT_OK
    assert "Prompt baseline_v1 | model fake-model" in output
    assert "Filters: language=None max_pages=600" in output
    assert "1. Hyperion by Dan Simmons" in output
    assert "   Reason: Layered world-building like Dune." in output
    assert "https://books.google.com/books?id=hyperion" in output
    assert "Checks: exists=pass, not in library=pass, max pages=pass, cites history=pass" in output
    assert "Result: PASSED" in output
    assert "Tokens: 1500 in, 120 out" in output
    assert "Latency: LLM " in output and "validation " in output


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
