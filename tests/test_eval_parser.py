import json
from pathlib import Path

import pytest

from book_agent.config import get_settings
from book_agent.evals import __main__ as evals_cli
from book_agent.evals.cases import LoadedCases, ParserCase, ParserCaseSet, load_parser_cases
from book_agent.evals.pacing import QuotaExhausted
from book_agent.evals.parser_eval import (
    ParserOutcome,
    fold,
    load_records,
    new_records,
    pending,
    records_path,
    render_parser_report,
    run_parser_eval,
    score,
    scores,
)
from book_agent.llm.base import LLMResponse
from book_agent.prompts import request_parser_v2
from tests.fakes import FakeLLM, text_response

SANDERSON = ParserCase(
    id="fantasy-no-sanderson",
    request="A fantasy book around 700 pages, but nothing from Brandon Sanderson",
    has_preference=True,
    preference_contains=["fantasy", "700", "Sanderson"],
)
FILTERS_ONLY = ParserCase(
    id="filters-only", request="books in English under 250 pages", has_preference=False,
    language="en", max_pages=250,
)  # fmt: skip


def interpreted(
    preference: str | None, language: str | None = None, max_pages: int | None = None
) -> LLMResponse:
    return text_response(
        json.dumps({"preference": preference, "language": language, "max_pages": max_pages})
    )


def test_fold_ignores_case_and_accents() -> None:
    assert fold("Distopía") == fold("distopia") == "distopia"
    assert fold("INGLÉS") == "ingles"


def test_terms_survive_with_other_spelling_and_case() -> None:
    outcome = ParserOutcome(
        case_id="x", preference="Fantasía/FANTASY, around 700 pages, no sanderson"
    )
    assert score(SANDERSON, outcome).dropped_terms == []


def test_a_dropped_term_is_separate_from_filter_errors() -> None:
    outcome = ParserOutcome(case_id="x", preference="a fantasy book")
    result = score(SANDERSON, outcome)
    assert result.filters_ok  # preference present, no filters expected, none given
    assert result.dropped_terms == ["700", "Sanderson"]
    assert not result.passed


def test_filter_fields_are_scored_one_by_one() -> None:
    good = score(FILTERS_ONLY, ParserOutcome(case_id="x", language="en", max_pages=250))
    assert good.passed
    wrong = score(FILTERS_ONLY, ParserOutcome(case_id="x", preference="books", language="en"))
    assert (wrong.preference_ok, wrong.language_ok, wrong.max_pages_ok) == (False, True, False)


def test_an_interpretation_error_fails_every_field() -> None:
    result = score(FILTERS_ONLY, ParserOutcome(case_id="x", error="no JSON found in the reply"))
    assert (result.preference_ok, result.language_ok, result.max_pages_ok) == (False, False, False)


def write_cases(tmp_path: Path, cases: list[ParserCase]) -> Path:
    folder = tmp_path / "cases"
    folder.mkdir(exist_ok=True)
    content = {"version": "p_v1", "cases": [c.model_dump() for c in cases]}
    (folder / "p_v1.json").write_text(json.dumps(content), encoding="utf-8")
    return folder


async def test_run_saves_each_outcome_and_resumes(tmp_path: Path) -> None:
    loaded = load_parser_cases("p_v1", write_cases(tmp_path, [SANDERSON, FILTERS_ONLY]))
    path = records_path(tmp_path / "results", "p_v1", "request_parser_v2", "fake-model", None)
    records = new_records(loaded, "request_parser_v2", "fake-model", None, "abc")

    llm = FakeLLM(
        [interpreted("fantasy, 700 pages, nothing from Sanderson"), QuotaExhausted("daily")]
    )
    stopped = await run_parser_eval(
        pending(loaded, None), records, path, llm=llm, prompt=request_parser_v2.PROMPT
    )

    assert stopped == "daily"
    saved = load_records(path)
    assert saved is not None and [o.case_id for o in saved.outcomes] == ["fantasy-no-sanderson"]
    assert [c.id for c in pending(loaded, saved)] == ["filters-only"]
    assert llm.requests[0].temperature == 0.0
    assert llm.requests[0].system == request_parser_v2.SYSTEM
    assert path.name == "request_parser_v2-fake-model-default.json"


def test_a_changed_case_file_is_refused_once_it_has_results(tmp_path: Path) -> None:
    loaded = load_parser_cases("p_v1", write_cases(tmp_path, [SANDERSON]))
    records = new_records(loaded, "request_parser_v2", "m", None, None)
    records.outcomes.append(ParserOutcome(case_id=SANDERSON.id, preference="fantasy"))
    changed = load_parser_cases("p_v1", write_cases(tmp_path, [SANDERSON, FILTERS_ONLY]))
    with pytest.raises(ValueError, match="changed after it had results"):
        pending(changed, records)


def test_report_prints_accuracy_mismatches_and_every_preference(tmp_path: Path) -> None:
    loaded = load_parser_cases("p_v1", write_cases(tmp_path, [SANDERSON, FILTERS_ONLY]))
    records = new_records(loaded, "request_parser_v2", "fake-model", None, None)
    records.outcomes += [
        # Keeps every term but inverts the negation: invisible to term matching.
        ParserOutcome(case_id=SANDERSON.id, preference="fantasy around 700 pages by Sanderson"),
        ParserOutcome(case_id=FILTERS_ONLY.id, preference="books", language="en"),
    ]
    report = render_parser_report(records, scores(loaded, records))

    assert "| preference present | 1/2 |" in report
    assert "| max_pages | 1/2 |" in report
    assert "| preference terms kept | 1/1 cases |" in report
    assert "- filters-only: expected no preference, got 'books'" in report
    assert "- filters-only: max_pages expected 250, got None" in report
    assert "## Dropped preference terms\n\nNone." in report
    # Every preference is listed, passing or not, so a lost negation can be read.
    assert (
        "- fantasy-no-sanderson [ok]: 'A fantasy book around 700 pages, but nothing from "
        "Brandon Sanderson' -> 'fantasy around 700 pages by Sanderson'"
    ) in report
    assert "- filters-only [MISMATCH]:" in report


def test_cli_plans_without_calling_until_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cases = write_cases(tmp_path, [SANDERSON, FILTERS_ONLY])

    def from_tmp(version: str) -> LoadedCases[ParserCaseSet]:
        return load_parser_cases(version, cases)

    monkeypatch.setattr(evals_cli, "load_parser_cases", from_tmp)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    get_settings.cache_clear()
    try:
        code = evals_cli.main(["parser", "--cases", "p_v1", "--results", str(tmp_path / "r")])
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    assert code == evals_cli.EXIT_OK
    assert "| prompt request_parser_v2 |" in out  # v2 is the default
    assert "Runs: 0 already recorded, 2 to run" in out
    assert "Estimated LLM requests: 2" in out
    assert out.rstrip().endswith("Add --yes to start.")
    assert not (tmp_path / "r").exists()
