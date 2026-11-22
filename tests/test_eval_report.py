from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import HttpUrl

from book_agent.agent import ToolTrace, TurnTrace
from book_agent.domain.models import CatalogBook
from book_agent.domain.recommendations import (
    Check,
    CheckedRecommendation,
    Lookup,
    Recommendation,
    Rule,
    Unknown,
    ValidationResult,
    Violation,
)
from book_agent.evals import __main__ as evals_cli
from book_agent.evals.records import EvalRecord, RecordStore, RunConfig
from book_agent.evals.report import (
    build_report,
    infrastructure_failure,
    outcome,
    p95,
    render_markdown,
    search_counts,
    searched_one_per_turn,
    used_fallback,
)
from book_agent.llm.base import StopReason, Usage
from book_agent.llm.calls import LLMCall

BASELINE = RunConfig(system="baseline", prompt_id="baseline_v1", model="m")
AGENT = RunConfig(system="agent", prompt_id="agent_v2", model="m")


def item(found_by: Lookup | None = Lookup.SEARCH) -> CheckedRecommendation:
    return CheckedRecommendation(
        recommendation=Recommendation(title="T", author="A", reason="R"),
        match=CatalogBook(source_id="x", title="T", link=HttpUrl("https://example.com/x")),
        found_by=found_by,
        library_match=None,
        cited_titles=["Dune"],
        exists=Check.PASS,
        not_in_library=Check.PASS,
        language=Check.SKIPPED,
        max_pages=Check.SKIPPED,
        cites_library=Check.PASS,
    )


def validation(
    violations: Sequence[Rule] = (), unknowns: Sequence[Rule] = (), found_by: Lookup = Lookup.SEARCH
) -> ValidationResult:
    return ValidationResult(
        items=[item(found_by)],
        violations=[Violation(rule=r, index=0, message="x") for r in violations],
        unknowns=[Unknown(rule=r, index=0, message="x") for r in unknowns],
    )


def turn(iteration: int, searches: int) -> TurnTrace:
    tools = [ToolTrace(name="search_books", arguments={}, outcome="1 book: X")] * searches
    return TurnTrace(
        iteration=iteration, stop_reason=StopReason.TOOL_USE, output_tokens=20, text="", tools=tools
    )


def call(tokens_in: int = 100, tokens_out: int = 10) -> LLMCall:
    return LLMCall(
        name="agent",
        model="m",
        usage=Usage(input_tokens=tokens_in, output_tokens=tokens_out),
        latency_s=1.0,
    )


def record(
    case_id: str, config: RunConfig = AGENT, run_index: int = 1, **fields: object
) -> EvalRecord:
    base: dict[str, object] = {
        "case_set": "set_v1", "cases_sha256": "c" * 64, "profile_sha256": "p" * 64,
        "case_id": case_id, "run_index": run_index, "config": config, "git_commit": "abc",
        "started_at": datetime(2026, 11, 18, tzinfo=UTC), "duration_s": 10.0,
    }  # fmt: skip
    return EvalRecord.model_validate(base | fields)


ACCEPTED = record(
    "a", final_validation=validation(), iterations=2, submissions=1, calls=[call(), call()]
)
UNKNOWN_PAGES = record(
    "b", final_validation=validation(unknowns=[Rule.MAX_PAGES]), iterations=3, submissions=1
)
EXHAUSTED = record(
    "c", final_validation=validation([Rule.ALREADY_READ]), error_kind="iterations_exhausted",
    rejections=[validation([Rule.ALREADY_READ]), validation(unknowns=[Rule.SAME_WORK])],
    iterations=7, submissions=2, repair_turn_used=True, duration_s=30.0,
    turns=[turn(1, 1), turn(2, 1), turn(3, 0)],
)  # fmt: skip
INTERPRETATION = record("d", error_kind="interpretation", error="no JSON")


def test_outcomes() -> None:
    assert outcome(ACCEPTED) == "accepted"
    assert outcome(UNKNOWN_PAGES) == "accepted"  # unknown page counts are acceptable
    assert outcome(EXHAUSTED) == "iterations_exhausted"
    assert outcome(INTERPRETATION) == "interpretation"
    assert outcome(record("e", final_validation=validation([Rule.MAX_PAGES]))) == "rejected"


def test_search_behavior() -> None:
    parallel = record("x", turns=[turn(1, 3), turn(2, 1)])
    assert search_counts(EXHAUSTED) == [1, 1]
    assert searched_one_per_turn(EXHAUSTED)
    assert not searched_one_per_turn(parallel)
    assert not searched_one_per_turn(record("y", turns=[turn(1, 1)]))  # one search turn only


def test_fallback_use_is_read_from_found_by() -> None:
    rescued = record("x", final_validation=validation(found_by=Lookup.FALLBACK))
    assert used_fallback(rescued) and not used_fallback(ACCEPTED)


def test_config_stats_aggregate_the_records() -> None:
    report = build_report("set_v1", [ACCEPTED, UNKNOWN_PAGES, EXHAUSTED, INTERPRETATION])
    [stats] = report.configs
    assert (stats.runs, stats.accepted) == (4, 2)
    assert stats.accept_rate == 0.5
    assert stats.outcomes == {"accepted": 2, "iterations_exhausted": 1, "interpretation": 1}
    assert stats.violations == {"already_read": 1}
    assert stats.unknowns == {"max_pages": 1}
    assert stats.rejection_reasons == {"already_read": 1, "same_work": 1}
    assert (stats.mean_iterations, stats.max_iterations) == (3.0, 7)
    assert stats.one_search_per_turn == 1 and stats.searches_per_search_turn == 1.0
    assert stats.mean_requests == 0.5 and stats.total_tokens == 220
    assert stats.repair_turn_used == 1 and stats.fallback_used == 0
    assert stats.p95_latency_s == 30.0
    assert stats.warnings == []


def failed_call(status: int | None = None) -> LLMCall:
    return LLMCall(name="agent", model=None, usage=None, latency_s=1.0, error_status=status)


GEMINI_503 = "LLM call failed: Gemini API error 503 UNAVAILABLE: high demand"
OUTAGE = record("o", error_kind="llm_error", error=GEMINI_503, calls=[call(), failed_call(503)])
CATALOG_DOWN = record(
    "p", error_kind="catalog_unavailable", final_validation=validation(unknowns=[Rule.NOT_FOUND])
)


@pytest.mark.parametrize(
    "run",
    [
        OUTAGE,
        CATALOG_DOWN,
        # Older records: no error_status, the status is only in the error text.
        record("q", error_kind="llm_error", error=GEMINI_503, calls=[failed_call()]),
        # A 5xx while interpreting the request counts too.
        record("r", error_kind="interpretation", error="x", calls=[failed_call(500)]),
    ],
)
def test_infrastructure_failures(run: EvalRecord) -> None:
    assert infrastructure_failure(run)


@pytest.mark.parametrize(
    "run",
    [
        ACCEPTED,
        EXHAUSTED,
        INTERPRETATION,
        record("s", error_kind="llm_error", error="400 bad", calls=[failed_call(400)]),
        record("t", error_kind="llm_error", error="refused: SAFETY", calls=[failed_call()]),
        # The last call succeeded: the run failed for another reason.
        record("u", error_kind="token_limit", error=GEMINI_503, calls=[call()]),
    ],
)
def test_not_infrastructure_failures(run: EvalRecord) -> None:
    assert not infrastructure_failure(run)


def test_both_acceptance_rates_are_reported() -> None:
    report = build_report("set_v1", [ACCEPTED, EXHAUSTED, OUTAGE, CATALOG_DOWN])
    [stats] = report.configs
    assert (stats.accepted, stats.runs, stats.infrastructure_failures) == (1, 4, 2)
    assert stats.accept_rate == 0.25
    assert stats.accept_rate_without_infrastructure == 0.5
    markdown = render_markdown(report)
    assert "| 4 | 1 (25%) | 1/2 (50%) |" in markdown
    assert "- Infrastructure failures (left out of the second rate): 2" in markdown


def test_rescoring_with_a_different_policy_needs_no_new_runs() -> None:
    records = [ACCEPTED, UNKNOWN_PAGES]

    def strict(v: ValidationResult) -> bool:  # only fully verified results
        return v.fully_verified

    assert build_report("set_v1", records).configs[0].accepted == 2
    assert build_report("set_v1", records, policy=strict).configs[0].accepted == 1


def test_per_case_table_counts_each_configuration() -> None:
    records = [
        ACCEPTED,
        record("a", run_index=2, final_validation=validation([Rule.MAX_PAGES])),
        record("a", config=BASELINE, final_validation=validation()),
    ]
    report = build_report("set_v1", records)
    assert report.per_case == {"a": {AGENT.id: (1, 2), BASELINE.id: (1, 1)}}


def test_warnings_for_mixed_runs() -> None:
    records = [ACCEPTED, record("b", git_commit="def", git_dirty=True, profile_sha256="q" * 64)]
    [stats] = build_report("set_v1", records).configs
    assert stats.warnings == [
        "runs come from 2 git commits",
        "1 runs had uncommitted changes",
        "runs used different libraries",
    ]


def test_markdown_has_both_tables_and_marks_agent_only_metrics() -> None:
    baseline = record("a", config=BASELINE, final_validation=validation())
    markdown = render_markdown(build_report("set_v1", [ACCEPTED, baseline]))
    assert "# Eval report: set_v1" in markdown
    assert "| agent-agent_v2-m-default | 1 | 1 (100%) | 1/1 (100%) | 2.0 / 2 |" in markdown
    assert "| baseline-baseline_v1-m-default | 1 | 1 (100%) | 1/1 (100%) | n/a |" in markdown
    assert "| a | 1/1 | 1/1 |" in markdown
    assert render_markdown(build_report("set_v1", [])).endswith("No records yet.")


@pytest.mark.parametrize(
    ("values", "expected"), [([], 0.0), ([5.0], 5.0), (list(map(float, range(1, 21))), 19.0)]
)
def test_p95(values: list[float], expected: float) -> None:
    assert p95(values) == expected


def test_report_command_reads_records_and_writes_markdown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = RecordStore(tmp_path)
    store.save(ACCEPTED)
    code = evals_cli.main(["report", "--cases", "set_v1", "--results", str(tmp_path)])
    assert code == evals_cli.EXIT_OK
    assert "# Eval report: set_v1" in capsys.readouterr().out
    assert (
        (tmp_path / "set_v1" / "report.md").read_text(encoding="utf-8").startswith("# Eval report")
    )


def test_per_case_outcomes_break_failures_down() -> None:
    records = [
        ACCEPTED,
        record("a", run_index=2, final_validation=validation([Rule.MAX_PAGES])),
        record(
            "a",
            run_index=3,
            error_kind="catalog_unavailable",
            final_validation=validation(unknowns=[Rule.NOT_FOUND]),
        ),
        EXHAUSTED,
        record("a", config=BASELINE, error_kind="unparseable"),
    ]
    report = build_report("set_v1", records)
    assert report.per_case_outcomes["a"][AGENT.id] == {
        "accepted": 1,
        "rejected": 1,
        "catalog_unavailable": 1,
    }
    assert report.per_case_outcomes["c"][AGENT.id] == {"iterations_exhausted": 1}

    markdown = render_markdown(report)
    assert "## Per case outcomes" in markdown
    header = (
        "| Case | Configuration | accepted | rejected | catalog_unavailable "
        "| iterations_exhausted | unparseable |"
    )
    assert header in markdown
    assert f"| a | {AGENT.id} | 1 | 1 | 1 | 0 | 0 |" in markdown
    assert f"| a | {BASELINE.id} | 0 | 0 | 0 | 0 | 1 |" in markdown
    assert f"| c | {AGENT.id} | 0 | 0 | 0 | 1 | 0 |" in markdown
