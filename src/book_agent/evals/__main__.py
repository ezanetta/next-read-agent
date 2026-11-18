"""Eval harness command line.

    uv run python -m book_agent.evals run --system baseline|agent [--prompt agent_v2]
        [--cases recommend_v1] [--runs 3] [--max-rpm 15] [--yes]

run prints the plan and an estimate of LLM requests, and starts only with --yes.
Exit codes: 0 done (or nothing to do), 1 stopped early by quota (rerun to resume), 2 errors.
"""

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

from book_agent.adapters.goodreads import load_goodreads_export
from book_agent.agent_run import AGENT_PROMPTS, DEFAULT_AGENT_PROMPT
from book_agent.config import Settings, get_settings
from book_agent.evals.cases import load_recommend_cases
from book_agent.evals.pacing import DEFAULT_MAX_RPM, PacedLLM
from book_agent.evals.records import RESULTS_DIR, RecordStore, RunConfig, System
from book_agent.evals.runner import (
    Plan,
    PlannedRun,
    RequestEstimate,
    RunSummary,
    estimate_requests,
    git_state,
    plan_runs,
    run_plan,
)
from book_agent.prompts import baseline_v1
from book_agent.wiring import build_llm, catalog_services
from book_agent.work_keys import DEFAULT_CACHE_PATH, load_cache

DEFAULT_CSV = Path("data/goodreads_library_export.csv")
DEFAULT_CASES = "recommend_v1"
DAILY_FREE_TIER = 500  # gemini-3.5-flash-lite requests per day
EXIT_OK, EXIT_STOPPED, EXIT_ERROR = 0, 1, 2


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "run":
        return _run_command(args)
    raise AssertionError(f"unhandled command {args.command!r}")


def _run_command(args: argparse.Namespace) -> int:
    system: System = args.system
    try:
        loaded = load_recommend_cases(args.cases)
        settings = get_settings()
        prompt = AGENT_PROMPTS[args.prompt] if system == "agent" else None
        config = run_config(system, prompt.prompt_id if prompt else baseline_v1.PROMPT_ID, settings)
        store = RecordStore(args.results)
        plan = plan_runs(loaded, config, args.runs, store)
        profile = load_goodreads_export(args.csv, profile_name="Me")
        cache = load_cache(args.cache)
    except (OSError, ValueError) as e:  # missing or changed cases, bad CSV, invalid settings
        print(f"error: {e}")
        return EXIT_ERROR

    print(format_plan(loaded.cases.version, len(loaded.cases.cases), args.runs, config, plan))
    if not plan.to_run:
        print("Nothing to run: every planned run is already recorded.")
        return EXIT_OK
    if not args.yes:
        print("Add --yes to start.")
        return EXIT_OK

    llm = PacedLLM(build_llm(settings), max_rpm=args.max_rpm)

    async def execute() -> RunSummary:
        async with catalog_services(settings, profile, cache, args.cache) as services:
            for note in services.notes:
                print(note)
            return await run_plan(
                plan,
                loaded,
                config,
                prompt=prompt,
                profile=profile,
                llm=llm,
                books=services.books,
                works=services.works,
                store=store,
                git=git_state(),
                requests_sent=lambda: llm.requests,
                progress=_print_progress,
            )

    summary = asyncio.run(execute())
    print(format_summary(summary, plan, store.root / loaded.cases.version / config.id))
    return EXIT_STOPPED if summary.stopped else EXIT_OK


def run_config(system: System, prompt_id: str, settings: Settings) -> RunConfig:
    return RunConfig(
        system=system,
        prompt_id=prompt_id,
        model=settings.gemini_model,
        thinking_level=settings.gemini_thinking_level,
    )


def format_plan(case_set: str, cases: int, runs: int, config: RunConfig, plan: Plan) -> str:
    estimate = estimate_requests(plan, config)
    return "\n".join(
        [
            f"Case set: {case_set} ({cases} cases)",
            f"Configuration: {config.id}",
            f"Runs: {plan.total} planned ({cases} cases x {runs}), "
            f"{plan.already_recorded} already recorded, {len(plan.to_run)} to run",
            f"Estimated LLM requests: {_estimate(estimate)} (free tier: {DAILY_FREE_TIER} per day)",
        ]
    )


def _estimate(estimate: RequestEstimate) -> str:
    return f"min {estimate.minimum}, typical {estimate.typical}, max {estimate.maximum}"


def format_summary(summary: RunSummary, plan: Plan, folder: Path) -> str:
    lines = [f"Done: {summary.done} of {len(plan.to_run)} runs, {summary.requests} LLM requests."]
    if summary.stopped:
        lines.append(f"Stopped early: {summary.stopped}")
        lines.append("Run the same command again later to resume; finished runs are kept.")
    lines.append(f"Records: {folder}")
    return "\n".join(lines)


def _print_progress(number: int, total: int, planned: PlannedRun) -> None:
    print(f"[{number}/{total}] {planned.case.id} run {planned.run_index}", flush=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m book_agent.evals")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="run a system on a case set and record each run")
    run.add_argument("--system", choices=["baseline", "agent"], required=True)
    run.add_argument(
        "--prompt",
        choices=sorted(AGENT_PROMPTS),
        default=DEFAULT_AGENT_PROMPT.prompt_id,
        help="agent prompt version (ignored for the baseline)",
    )
    run.add_argument("--cases", default=DEFAULT_CASES, help="case set version")
    run.add_argument("--runs", type=int, default=3, help="runs per case")
    run.add_argument("--max-rpm", type=int, default=DEFAULT_MAX_RPM, help="requests per minute")
    run.add_argument("--yes", action="store_true", help="start the runs (otherwise plan only)")
    run.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="Goodreads export")
    run.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH, help="work key cache")
    run.add_argument("--results", type=Path, default=RESULTS_DIR, help="results directory")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
