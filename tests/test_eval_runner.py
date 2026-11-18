import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from book_agent.config import get_settings
from book_agent.domain.recommendations import Filters, Recommendation
from book_agent.evals import __main__ as evals_cli
from book_agent.evals.cases import LoadedCases, RecommendCaseSet, load_recommend_cases
from book_agent.evals.pacing import QuotaExhausted
from book_agent.evals.records import RecordStore, RunConfig
from book_agent.evals.runner import GitState, RunSummary, estimate_requests, plan_runs, run_plan
from book_agent.llm.base import LLMResponse, ToolCallPart
from book_agent.prompts import agent_v2
from tests.fakes import FakeLLM, text_response, tool_call_response
from tests.library_data import CLEAN_THREE, PROFILE, catalog

BASELINE = RunConfig(system="baseline", prompt_id="baseline_v1", model="fake-model")
AGENT = RunConfig(system="agent", prompt_id="agent_v2", model="fake-model")
GIT = GitState(commit="abc123", dirty=False)


def write_cases(tmp_path: Path, cases: Sequence[Mapping[str, object]]) -> Path:
    folder = tmp_path / "cases"
    folder.mkdir(exist_ok=True)
    (folder / "set_v1.json").write_text(
        json.dumps({"version": "set_v1", "cases": cases}), encoding="utf-8"
    )
    return folder


CASES: list[dict[str, object]] = [
    {"id": "library-only", "category": "pure", "note": "n"},
    {
        "id": "a-classic",
        "request": "a classic",
        "flags": {"max_pages": 600},
        "category": "c",
        "note": "n",
    },
]


def reply(recommendations: list[Recommendation]) -> LLMResponse:
    return text_response(json.dumps({"recommendations": [r.model_dump() for r in recommendations]}))


def interpreted(preference: str) -> LLMResponse:
    return text_response(
        json.dumps({"preference": preference, "language": None, "max_pages": None})
    )


def submit() -> LLMResponse:
    return tool_call_response(
        ToolCallPart(
            id="submit-1",
            name="submit_recommendations",
            arguments={"recommendations": [r.model_dump() for r in CLEAN_THREE]},
        )
    )


async def run(
    tmp_path: Path, config: RunConfig, script: list[LLMResponse | Exception], runs: int = 1
) -> tuple[RecordStore, RunSummary]:
    loaded = load_recommend_cases("set_v1", write_cases(tmp_path, CASES))
    store = RecordStore(tmp_path / "results")
    plan = plan_runs(loaded, config, runs, store)
    summary = await run_plan(
        plan,
        loaded,
        config,
        prompt=agent_v2.PROMPT if config.system == "agent" else None,
        profile=PROFILE,
        llm=FakeLLM(script),
        books=catalog(),
        works=None,
        store=store,
        git=GIT,
    )
    return store, summary


async def test_baseline_records_are_complete(tmp_path: Path) -> None:
    store, _ = await run(
        tmp_path, BASELINE, [reply(CLEAN_THREE), interpreted("a classic"), reply(CLEAN_THREE)]
    )

    records = {r.case_id: r for r in store.load_all("set_v1")}
    assert set(records) == {"library-only", "a-classic"}
    classic = records["a-classic"]
    assert classic.config == BASELINE and classic.run_index == 1
    assert classic.git_commit == "abc123" and not classic.git_dirty
    assert classic.request is not None and classic.request.filters == Filters(max_pages=600)
    assert classic.request_prompt_id == "request_parser_v3"
    assert classic.final_validation is not None and classic.final_validation.passed
    assert classic.recommendations is not None and classic.raw_text is not None
    assert [c.name for c in classic.calls] == ["interpret_request", "recommend"]
    assert (classic.submissions, classic.iterations, classic.error_kind) == (1, 0, None)
    assert len(classic.cases_sha256) == 64 and len(classic.profile_sha256) == 64


async def test_agent_records_are_complete(tmp_path: Path) -> None:
    store, _ = await run(tmp_path, AGENT, [submit(), interpreted("a classic"), submit()])

    record = next(r for r in store.load_all("set_v1") if r.case_id == "a-classic")
    assert record.feedback_prompt_id == "submission_feedback_v1"
    assert record.final_validation is not None and record.final_validation.passed
    assert len(record.turns) == 1 and record.turns[0].tools[0].outcome == "accepted"
    assert (record.iterations, record.submissions, record.repair_turn_used) == (1, 1, False)
    assert [c.name for c in record.calls] == ["interpret_request", "agent"]


async def test_failures_are_recorded_with_their_kind(tmp_path: Path) -> None:
    store, _ = await run(tmp_path, BASELINE, [text_response("not json"), text_response("Sure!")])
    kinds = {r.case_id: r.error_kind for r in store.load_all("set_v1")}
    assert kinds == {"library-only": "unparseable", "a-classic": "interpretation"}


async def test_resume_skips_recorded_runs(tmp_path: Path) -> None:
    store, _ = await run(
        tmp_path, BASELINE, [reply(CLEAN_THREE), interpreted("x"), reply(CLEAN_THREE)]
    )
    loaded = load_recommend_cases("set_v1", tmp_path / "cases")
    plan = plan_runs(loaded, BASELINE, 2, store)
    assert plan.already_recorded == 2
    assert [(p.case.id, p.run_index) for p in plan.to_run] == [
        ("library-only", 2),
        ("a-classic", 2),
    ]


async def test_a_quota_stop_keeps_finished_runs(tmp_path: Path) -> None:
    store, summary = await run(
        tmp_path, BASELINE, [reply(CLEAN_THREE), QuotaExhausted("daily quota exhausted: 429")]
    )
    assert [r.case_id for r in store.load_all("set_v1")] == ["library-only"]
    assert summary.done == 1
    assert summary.stopped == "daily quota exhausted: 429"
    # Rerunning resumes with the interrupted case.
    plan = plan_runs(load_recommend_cases("set_v1", tmp_path / "cases"), BASELINE, 1, store)
    assert [p.case.id for p in plan.to_run] == ["a-classic"]


async def test_a_changed_case_file_is_refused_once_it_has_results(tmp_path: Path) -> None:
    store, _ = await run(
        tmp_path, BASELINE, [reply(CLEAN_THREE), interpreted("x"), reply(CLEAN_THREE)]
    )
    write_cases(tmp_path, [*CASES, {"id": "new-case", "category": "c", "note": "n"}])
    loaded = load_recommend_cases("set_v1", tmp_path / "cases")
    with pytest.raises(ValueError, match="set_v1 changed after it had results; it is frozen"):
        plan_runs(loaded, BASELINE, 1, store)


async def test_agent_runs_need_a_prompt(tmp_path: Path) -> None:
    loaded = load_recommend_cases("set_v1", write_cases(tmp_path, CASES))
    store = RecordStore(tmp_path / "results")
    with pytest.raises(ValueError, match="need a prompt"):
        await run_plan(
            plan_runs(loaded, AGENT, 1, store), loaded, AGENT, prompt=None, profile=PROFILE,
            llm=FakeLLM([]), books=catalog(), works=None, store=store, git=GIT,
        )  # fmt: skip


def test_estimate_counts_interpretation_only_for_cases_with_text(tmp_path: Path) -> None:
    loaded = load_recommend_cases("set_v1", write_cases(tmp_path, CASES))
    plan = plan_runs(loaded, BASELINE, 3, RecordStore(tmp_path / "results"))
    baseline = estimate_requests(plan, BASELINE)
    # 3 runs of library-only (1 call) + 3 of a-classic (2 calls), plus judge calls.
    assert (baseline.minimum, baseline.typical, baseline.maximum) == (9, 15, 27)
    agent = estimate_requests(plan, AGENT)
    assert agent.minimum == 9
    assert agent.typical == 3 * 4 + 3 * 5
    assert agent.maximum == 3 * 28 + 3 * 29


def test_cli_prints_the_plan_and_needs_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    get_settings.cache_clear()
    try:
        cases = write_cases(tmp_path, CASES)

        def from_tmp(version: str) -> LoadedCases[RecommendCaseSet]:
            return load_recommend_cases(version, cases)

        monkeypatch.setattr(evals_cli, "load_recommend_cases", from_tmp)
        code = evals_cli.main(
            ["run", "--system", "agent", "--cases", "set_v1", "--runs", "2",
             "--csv", "tests/fixtures/goodreads_sample.csv", "--results", str(tmp_path / "r")]
        )  # fmt: skip
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    assert code == evals_cli.EXIT_OK
    assert "Runs: 4 planned (2 cases x 2), 0 already recorded, 4 to run" in out
    assert "Estimated LLM requests: min 6, typical 18" in out
    assert out.rstrip().endswith("Add --yes to start.")
    assert not (tmp_path / "r").exists()  # nothing ran, nothing written
