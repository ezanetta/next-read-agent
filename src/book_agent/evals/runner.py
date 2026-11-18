"""Running the systems on a case set and saving one record per run.

The runner never scores: it records (D-021). Runs are sequential and resumable: a run
whose record exists is skipped, and a quota stop keeps every finished run.
"""

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter

from pydantic import BaseModel

from book_agent.agent import MAX_TURNS
from book_agent.agent_run import run_agent_pipeline
from book_agent.baseline import run_baseline
from book_agent.domain.models import Profile
from book_agent.evals.cases import LoadedCases, RecommendCase, RecommendCaseSet
from book_agent.evals.pacing import QuotaExhausted
from book_agent.evals.records import EvalRecord, RecordStore, RunConfig
from book_agent.llm.base import LLMClient
from book_agent.ports import BookSearch, WorkResolver
from book_agent.prompts.agent_prompt import AgentPrompt

# Typical counts from the real runs (F-006): 2 to 4 agent turns, rarely a judge call.
_TYPICAL_AGENT_TURNS = 3
_TYPICAL_JUDGE_CALLS = 1
_MAX_JUDGE_CALLS_PER_SUBMISSION = 3  # one per recommendation by a library author


@dataclass(frozen=True)
class PlannedRun:
    case: RecommendCase
    run_index: int


@dataclass(frozen=True)
class Plan:
    to_run: list[PlannedRun]
    already_recorded: int

    @property
    def total(self) -> int:
        return len(self.to_run) + self.already_recorded


class RequestEstimate(BaseModel, frozen=True):
    """LLM requests the planned runs may make: a floor, a typical count and a ceiling."""

    minimum: int
    typical: int
    maximum: int


@dataclass
class RunSummary:
    done: int = 0
    stopped: str | None = None  # why the run stopped early, if it did
    requests: int = 0  # LLM requests sent, retries included


@dataclass(frozen=True)
class GitState:
    commit: str | None
    dirty: bool


def git_state() -> GitState:
    """The current commit and whether tracked files have uncommitted changes."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return GitState(commit=None, dirty=False)
    return GitState(commit=commit or None, dirty=bool(status.strip()))


def plan_runs(
    loaded: LoadedCases[RecommendCaseSet], config: RunConfig, runs: int, store: RecordStore
) -> Plan:
    """Every (case, run index) pair not yet recorded for this configuration.

    Raises ValueError if the case file changed after results exist (D-023).
    """
    case_set = loaded.cases.version
    existing = store.cases_hashes(case_set)
    if existing and existing != {loaded.sha256}:
        raise ValueError(
            f"{case_set} changed after it had results; it is frozen. "
            "Put the changes in a new version (e.g. a _v2 file)."
        )
    to_run: list[PlannedRun] = []
    recorded = 0
    for case in loaded.cases.cases:
        for run_index in range(1, runs + 1):
            if store.exists(case_set, config, case.id, run_index):
                recorded += 1
            else:
                to_run.append(PlannedRun(case=case, run_index=run_index))
    return Plan(to_run=to_run, already_recorded=recorded)


def estimate_requests(plan: Plan, config: RunConfig) -> RequestEstimate:
    minimum = typical = maximum = 0
    for planned in plan.to_run:
        interpret = 1 if planned.case.request else 0
        if config.system == "baseline":
            minimum += interpret + 1
            typical += interpret + 1 + _TYPICAL_JUDGE_CALLS
            maximum += interpret + 1 + _MAX_JUDGE_CALLS_PER_SUBMISSION
        else:
            minimum += interpret + 1
            typical += interpret + _TYPICAL_AGENT_TURNS + _TYPICAL_JUDGE_CALLS
            maximum += interpret + MAX_TURNS * (1 + _MAX_JUDGE_CALLS_PER_SUBMISSION)
    return RequestEstimate(minimum=minimum, typical=typical, maximum=maximum)


async def run_plan(
    plan: Plan,
    loaded: LoadedCases[RecommendCaseSet],
    config: RunConfig,
    *,
    prompt: AgentPrompt | None,
    profile: Profile,
    llm: LLMClient,
    books: BookSearch,
    works: WorkResolver | None,
    store: RecordStore,
    git: GitState,
    requests_sent: Callable[[], int] = lambda: 0,
    progress: Callable[[int, int, PlannedRun], None] | None = None,
) -> RunSummary:
    """Run each planned case and save its record. A quota stop ends the loop cleanly:
    the interrupted run leaves no record, so a rerun of the same plan resumes there."""
    if config.system == "agent" and prompt is None:
        raise ValueError("agent runs need a prompt version")
    profile_sha = store.save_profile(loaded.cases.version, profile)
    summary = RunSummary()
    for number, planned in enumerate(plan.to_run, start=1):
        if progress is not None:
            progress(number, len(plan.to_run), planned)
        started_at = datetime.now(UTC)
        started = perf_counter()
        try:
            fields = await _run_case(planned.case, config, prompt, profile, llm, books, works)
        except QuotaExhausted as e:
            summary.stopped = str(e)
            break
        record = EvalRecord.model_validate(
            {
                "case_set": loaded.cases.version,
                "cases_sha256": loaded.sha256,
                "profile_sha256": profile_sha,
                "case_id": planned.case.id,
                "run_index": planned.run_index,
                "config": config,
                "git_commit": git.commit,
                "git_dirty": git.dirty,
                "started_at": started_at,
                "duration_s": perf_counter() - started,
                **fields,
            }
        )
        store.save(record)
        summary.done += 1
    summary.requests = requests_sent()
    return summary


async def _run_case(
    case: RecommendCase,
    config: RunConfig,
    prompt: AgentPrompt | None,
    profile: Profile,
    llm: LLMClient,
    books: BookSearch,
    works: WorkResolver | None,
) -> dict[str, object]:
    """Run one case and return the record fields it produced."""
    if config.system == "baseline":
        result = await run_baseline(
            profile, case.flags, request=case.request, llm=llm, books=books, works=works
        )
        return {
            "request_prompt_id": result.request_prompt_id,
            "request": result.request,
            "error_kind": result.error_kind,
            "error": result.error,
            "recommendations": result.parsed,
            "raw_text": result.raw_text,
            "final_validation": result.validation,
            "calls": result.calls,
            "submissions": 1 if result.validation is not None else 0,
        }
    assert prompt is not None
    run = await run_agent_pipeline(
        profile, case.flags, request=case.request, llm=llm, books=books, works=works, prompt=prompt
    )
    if run.failure is not None:
        error_kind: str | None = run.failure.value
    else:
        error_kind = "interpretation" if run.error is not None else None
    return {
        "feedback_prompt_id": run.feedback_prompt_id,
        "request_prompt_id": run.request_prompt_id,
        "request": run.request,
        "error_kind": error_kind,
        "error": run.error,
        "recommendations": run.recommendations,
        "raw_text": run.raw_text,
        "final_validation": run.validation or run.last_validation,
        "rejections": run.rejections,
        "turns": run.turns,
        "calls": run.calls,
        "iterations": run.iterations,
        "submissions": run.submissions,
        "repair_turn_used": run.repair_turn_used,
    }
