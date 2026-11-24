"""Eval harness command line.

    uv run python -m book_agent.evals run --system baseline|agent [--prompt agent_v2]
        [--cases recommend_v1] [--runs 3] [--max-rpm 15] [--yes]
    uv run python -m book_agent.evals report [--cases recommend_v1] [--judge JUDGE_ID]
    uv run python -m book_agent.evals parser [--cases request_parser_v2]
        [--prompt request_parser_v2] [--yes]
    uv run python -m book_agent.evals sample [--harness 2] [--seed N]
    uv run python -m book_agent.evals label [--sample calibration_v1]
    uv run python -m book_agent.evals relabel --book N --note TEXT [--fits y|p|n]
        [--quality y|p|n] [--would-read y|p|n]
    uv run python -m book_agent.evals judge [--harness 2] [--model M] [--yes]
    uv run python -m book_agent.evals calibrate --split tuning|held-out [--model M] [--yes]

run prints the plan and an estimate of LLM requests, and starts only with --yes.
report reads the records only (no LLM calls) and writes data/evals/<cases>/report.md.
parser interprets each labeled request once (temperature 0) and reports per-field
accuracy, dropped preference terms, and every interpreted preference.
sample draws the frozen calibration sample; label asks for your labels, one blind book
at a time; relabel corrects one tuning-half label, with a note (D-036); judge grades
every matched book of a round's records; calibrate scores the judge against your labels
on one half of the sample (D-033, D-034).
Exit codes: 0 done (or nothing to do), 1 stopped early by quota (rerun to resume), 2 errors.
"""

import argparse
import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from book_agent.adapters.goodreads import load_goodreads_export
from book_agent.agent_run import AGENT_PROMPTS, DEFAULT_AGENT_PROMPT
from book_agent.baseline import baseline_prompt
from book_agent.config import Settings, get_settings
from book_agent.evals.backups import backup_dir
from book_agent.evals.calibration import (
    CALIBRATION_DIR,
    DEFAULT_SAMPLE,
    HELD_OUT,
    PER_SYSTEM,
    TUNING,
    CalibrationSample,
    CalibrationStore,
    Split,
    correct_label,
    draw_sample,
    label_session,
    render_calibration,
    shared_questions,
)
from book_agent.evals.cases import ParserCase, load_parser_cases, load_recommend_cases
from book_agent.evals.citation_report import render_citation_section
from book_agent.evals.judge import (
    DEFAULT_JUDGE_MODEL,
    DEFAULT_JUDGE_PROMPT,
    JUDGE_PROMPTS,
    BookRef,
    JudgeConfig,
    JudgmentStore,
    PlannedBook,
    Verdict,
    judge_input,
    judgeable_items,
    plan_books,
    retried_errors,
    run_judge,
)
from book_agent.evals.judge_report import render_judge_section
from book_agent.evals.pacing import DEFAULT_MAX_RPM, PacedLLM
from book_agent.evals.parser_eval import (
    load_records,
    new_records,
    pending,
    records_path,
    render_parser_report,
    run_parser_eval,
    scores,
)
from book_agent.evals.records import (
    HARNESS_VERSION,
    RESULTS_DIR,
    EvalRecord,
    RecordStore,
    RunConfig,
    System,
)
from book_agent.evals.report import build_report, render_markdown
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
from book_agent.files import write_atomic
from book_agent.llm.base import LLMClient
from book_agent.prompts.judge_v1 import JudgeInput
from book_agent.request_parser import DEFAULT_PARSER_PROMPT, PARSER_PROMPTS
from book_agent.wiring import (
    build_library_retriever,
    build_provider,
    catalog_services,
    with_retries,
)
from book_agent.work_keys import DEFAULT_CACHE_PATH, load_cache

DEFAULT_CSV = Path("data/goodreads_library_export.csv")
DEFAULT_CASES = "recommend_v1"
DEFAULT_PARSER_CASES = "request_parser_v2"  # labels min_pages (D-026); v1 stays frozen
JUDGED_HARNESS = 2  # round 2 (F-013): the records phase 2 judges
DEFAULT_SEED = 20261122
DAILY_FREE_TIER = 500  # gemini-3.5-flash-lite requests per day
EXIT_OK, EXIT_STOPPED, EXIT_ERROR = 0, 1, 2
_DEFAULT_JUDGE_ID = JudgeConfig(
    prompt_id=DEFAULT_JUDGE_PROMPT.prompt_id, model=DEFAULT_JUDGE_MODEL
).id


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "run":
        return _run_command(args)
    if args.command == "report":
        return _report_command(args)
    if args.command == "parser":
        return _parser_command(args)
    if args.command == "sample":
        return _sample_command(args)
    if args.command == "label":
        return _label_command(args)
    if args.command == "relabel":
        return _relabel_command(args)
    if args.command == "judge":
        return _judge_command(args)
    if args.command == "calibrate":
        return _calibrate_command(args)
    raise AssertionError(f"unhandled command {args.command!r}")


def _run_command(args: argparse.Namespace) -> int:
    system: System = args.system
    try:
        loaded = load_recommend_cases(args.cases)
        settings = get_settings()
        prompt = AGENT_PROMPTS[args.prompt] if system == "agent" else None
        config = run_config(
            system, prompt.prompt_id if prompt else baseline_prompt.PROMPT_ID, settings
        )
        store = RecordStore(args.results)
        plan = plan_runs(
            loaded, config, args.runs, store, rerun_infrastructure=args.rerun_infrastructure
        )
        profile = load_goodreads_export(args.csv, profile_name="Me")
        cache = load_cache(args.cache)
        # A prompt with library retrieval needs a built index: fail before any run (D-041).
        retriever = (
            build_library_retriever(settings, profile)
            if prompt is not None and prompt.uses_retrieval
            else None
        )
    except (OSError, ValueError) as e:  # missing or changed cases, bad CSV, invalid settings
        print(f"error: {e}")
        return EXIT_ERROR

    print(format_plan(loaded.cases.version, len(loaded.cases.cases), args.runs, config, plan))
    backup = backup_dir(store.root, loaded.cases.version, datetime.now(UTC))
    if plan.reruns:
        print(f"Reruns of infrastructure failures: {len(plan.reruns)} (backup: {backup})")
        for planned in plan.reruns:
            print(f"- {planned.case.id} run {planned.run_index} ({planned.replaces})")
    if retriever is not None:
        texts = {p.case.request for p in plan.to_run if p.case.request}
        print(
            f"Library retrieval: {len(texts)} distinct request texts, "
            f"at most {len(texts)} embedding requests (cached queries are free)"
        )
    if not plan.to_run:
        print("Nothing to run: every planned run is already recorded.")
        return EXIT_OK
    if not args.yes:
        print("Add --yes to start.")
        return EXIT_OK

    paced = PacedLLM(build_provider(settings), max_rpm=args.max_rpm)
    llm = with_retries(paced)  # retries go through pacing and are counted

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
                requests_sent=lambda: paced.requests,
                progress=_print_progress,
                retriever=retriever,
                backup=backup,
            )

    summary = asyncio.run(execute())
    print(format_summary(summary, plan, store.root / loaded.cases.version / config.id))
    return EXIT_STOPPED if summary.stopped else EXIT_OK


def _report_command(args: argparse.Namespace) -> int:
    store = RecordStore(args.results)
    try:
        records = store.load_all(args.cases)
    except ValueError as e:
        print(f"error: {e}")
        return EXIT_ERROR
    markdown = render_markdown(build_report(args.cases, records))
    markdown += "\n".join(render_citation_section(records)) + "\n"
    if args.judge:
        judgments = JudgmentStore(store).judged(args.cases, args.judge)
        markdown += "\n".join(render_judge_section(records, judgments, args.judge)) + "\n"
    print(markdown)
    if records:
        path = store.root / args.cases / "report.md"
        write_atomic(path, markdown)
        print(f"Written to {path}")
    return EXIT_OK


def _parser_command(args: argparse.Namespace) -> int:
    try:
        loaded = load_parser_cases(args.cases)
        settings = get_settings()
        prompt = PARSER_PROMPTS[args.prompt]
        path = records_path(
            args.results,
            args.cases,
            prompt.prompt_id,
            settings.gemini_model,
            settings.gemini_thinking_level,
        )
        records = load_records(path)
        to_run = pending(loaded, records)
    except (OSError, ValueError) as e:
        print(f"error: {e}")
        return EXIT_ERROR

    total = len(loaded.cases.cases)
    print(
        f"Case set: {args.cases} ({total} cases) | prompt {prompt.prompt_id} "
        f"| model {settings.gemini_model}"
    )
    print(f"Runs: {total - len(to_run)} already recorded, {len(to_run)} to run")
    print(f"Estimated LLM requests: {len(to_run)} (one per case, temperature 0)")
    if to_run and not args.yes:
        print("Add --yes to start.")
        return EXIT_OK

    if records is None:
        records = new_records(
            loaded,
            prompt.prompt_id,
            settings.gemini_model,
            settings.gemini_thinking_level,
            git_state().commit,
        )
    stopped: str | None = None
    if to_run:
        llm = with_retries(PacedLLM(build_provider(settings), max_rpm=args.max_rpm))
        stopped = asyncio.run(
            run_parser_eval(
                to_run, records, path, llm=llm, prompt=prompt, progress=_print_parser_progress
            )
        )

    markdown = render_parser_report(records, scores(loaded, records))
    print(markdown)
    if records.outcomes:
        report_path = path.with_suffix(".md")
        write_atomic(report_path, markdown)
        print(f"Written to {report_path}")
    if stopped:
        print(f"Stopped early: {stopped}")
        print("Run the same command again later to resume; finished cases are kept.")
        return EXIT_STOPPED
    return EXIT_OK


# --- Phase 2: the judge and its calibration ------------------------------------------


def judge_config(args: argparse.Namespace) -> JudgeConfig:
    return JudgeConfig(prompt_id=args.prompt, model=args.model, thinking_level=args.thinking)


def _judge_llm(settings: Settings, judge: JudgeConfig, max_rpm: int) -> tuple[PacedLLM, LLMClient]:
    """The judge's own model, which may differ from the one that made the runs."""
    judge_settings = settings.model_copy(
        update={"gemini_model": judge.model, "gemini_thinking_level": judge.thinking_level}
    )
    paced = PacedLLM(build_provider(judge_settings), max_rpm=max_rpm)
    return paced, with_retries(paced)


def _round_records(store: RecordStore, case_set: str, harness: int) -> list[EvalRecord]:
    return [r for r in store.load_all(case_set) if r.config.harness == harness]


def _sample_command(args: argparse.Namespace) -> int:
    store = RecordStore(args.results)
    calibration = CalibrationStore(args.calibration)
    try:
        records = _round_records(store, args.cases, args.harness)
        if not records:
            raise ValueError(f"no harness-{args.harness} records in {args.cases}")
        items = draw_sample(records, args.seed, per_system=args.per_system)
        sample = CalibrationSample(
            version=args.sample,
            case_set=args.cases,
            harness=args.harness,
            seed=args.seed,
            created_at=datetime.now(UTC),
            items=items,
        )
        path = calibration.save_sample(sample)
    except (OSError, ValueError) as e:
        print(f"error: {e}")
        return EXIT_ERROR
    print(format_sample(sample))
    print(f"Written to {path}")
    return EXIT_OK


def format_sample(sample: CalibrationSample) -> str:
    """Balance check: books per system and half, and the cases covered."""
    lines = [f"Sample {sample.version}: {len(sample.items)} books (seed {sample.seed})"]
    for system in sorted({i.system for i in sample.items}):
        mine = [i for i in sample.items if i.system == system]
        tuning = sum(i.split == TUNING for i in mine)
        cases = len({i.ref.case_id for i in mine})
        lines.append(
            f"- {system}: {len(mine)} books from {cases} cases, "
            f"{tuning} tuning / {len(mine) - tuning} held-out"
        )
    return "\n".join(lines)


def _sample_inputs(store: RecordStore, sample: CalibrationSample) -> dict[BookRef, JudgeInput]:
    records = {(r.config.id, r.case_id, r.run_index): r for r in store.load_all(sample.case_set)}
    inputs: dict[BookRef, JudgeInput] = {}
    for item in sample.items:
        ref = item.ref
        record = records.get((ref.config_id, ref.case_id, ref.run_index))
        if record is None:
            raise ValueError(f"the sample points at a missing record: {ref}")
        profile = store.load_profile(sample.case_set, record.profile_sha256)
        inputs[ref] = judge_input(record, ref.item_index, profile)
    return inputs


def _label_command(args: argparse.Namespace) -> int:
    store = RecordStore(args.results)
    calibration = CalibrationStore(args.calibration)
    try:
        sample, sha = calibration.load_sample(args.sample)
        inputs = _sample_inputs(store, sample)
        labels = calibration.load_labels(args.sample, sha)
    except (OSError, ValueError) as e:
        print(f"error: {e}")
        return EXIT_ERROR
    try:
        count = label_session(
            sample, inputs, labels, save=calibration.save_labels, ask=input, show=print
        )
    except (EOFError, KeyboardInterrupt):
        print()
        count = 0
    total = len(labels.labels)
    print(f"Labeled {count} this session; {total} of {len(sample.items)} in total.")
    print(f"Labels: {calibration.labels_path(args.sample)}")
    return EXIT_OK


_VERDICTS = {"y": Verdict.YES, "p": Verdict.PARTIAL, "n": Verdict.NO}


def _relabel_command(args: argparse.Namespace) -> int:
    calibration = CalibrationStore(args.calibration)
    try:
        sample, sha = calibration.load_sample(args.sample)
        labels = calibration.load_labels(args.sample, sha)
        before, after = correct_label(
            sample,
            labels,
            args.book,
            note=args.note,
            fits_request=_VERDICTS.get(args.fits or ""),
            reason_quality=_VERDICTS.get(args.quality or ""),
            would_read=_VERDICTS.get(args.would_read or ""),
        )
        calibration.save_labels(labels)
    except (OSError, ValueError) as e:
        print(f"error: {e}")
        return EXIT_ERROR
    ref = after.ref
    print(f"Book #{args.book} ({ref.case_id} run {ref.run_index} book {ref.item_index + 1}):")
    for name in ("fits_request", "reason_quality", "would_read"):
        old, new = getattr(before, name), getattr(after, name)
        if old != new:
            print(f"- {name}: {old} -> {new}")
    print(f"Note: {after.corrections[-1].note}")
    print(f"Saved to {calibration.labels_path(args.sample)}; the earlier labels stay in history.")
    return EXIT_OK


def _judge_command(args: argparse.Namespace) -> int:
    store = RecordStore(args.results)
    judgments = JudgmentStore(store)
    judge = judge_config(args)
    try:
        settings = get_settings()
        records = _round_records(store, args.cases, args.harness)
        plan = plan_books(records, judgments, args.cases, judge)
    except (OSError, ValueError) as e:
        print(f"error: {e}")
        return EXIT_ERROR
    books = sum(len(judgeable_items(r)) for r in records)
    print(f"Case set: {args.cases} | harness {args.harness} | {len(records)} records")
    print(f"Judge: {judge.id}")
    print(f"Matched books: {books}, {books - len(plan)} already judged, {len(plan)} to judge")
    retries = retried_errors(plan, judgments, args.cases, judge)
    if retries:
        print(f"Of those, {retries} retry an earlier error (the old files are backed up, D-042)")
    print(f"Estimated LLM requests: {len(plan)} (free tier: {DAILY_FREE_TIER} per day)")
    return _judge_plan(args, settings, store, judgments, judge, plan)


def _judge_plan(
    args: argparse.Namespace,
    settings: Settings,
    store: RecordStore,
    judgments: JudgmentStore,
    judge: JudgeConfig,
    plan: list[PlannedBook],
) -> int:
    """Run a judging plan if --yes was given. Returns the exit code."""
    if not plan:
        return EXIT_OK
    if not args.yes:
        print("Add --yes to start.")
        return EXIT_OK
    paced, llm = _judge_llm(settings, judge, args.max_rpm)
    summary = asyncio.run(
        run_judge(
            plan,
            case_set=args.cases,
            judge=judge,
            profiles=lambda sha: store.load_profile(args.cases, sha),
            llm=llm,
            store=judgments,
            git_commit=git_state().commit,
            progress=_print_judge_progress,
            backup=backup_dir(store.root, args.cases, datetime.now(UTC)),
        )
    )
    print(
        f"Judged {summary.judged} of {len(plan)} books ({summary.errors} errors), "
        f"{paced.requests} LLM requests."
    )
    if summary.stopped:
        print(f"Stopped early: {summary.stopped}")
        print("Run the same command again later to resume; finished judgments are kept.")
        return EXIT_STOPPED
    return EXIT_OK


def _print_judge_progress(number: int, total: int, planned: PlannedBook) -> None:
    ref = planned.ref
    print(
        f"[{number}/{total}] {ref.case_id} run {ref.run_index} book {ref.item_index + 1}",
        flush=True,
    )


def _calibrate_command(args: argparse.Namespace) -> int:
    store = RecordStore(args.results)
    judgments = JudgmentStore(store)
    calibration = CalibrationStore(args.calibration)
    judge = judge_config(args)
    split: Split = HELD_OUT if args.split == "held-out" else TUNING
    try:
        settings = get_settings()
        sample, sha = calibration.load_sample(args.sample)
        labels = calibration.load_labels(args.sample, sha)
        refs = {i.ref for i in sample.split(split)}
        records = [r for r in store.load_all(sample.case_set) if r.config.harness == sample.harness]
        plan = plan_books(records, judgments, sample.case_set, judge, only=refs)
        shared = shared_questions(sample, _sample_inputs(store, sample))
    except (OSError, ValueError) as e:
        print(f"error: {e}")
        return EXIT_ERROR
    args.cases = sample.case_set  # _judge_plan reads it
    print(f"Sample {sample.version}, {args.split} half: {len(refs)} books")
    print(f"Judge: {judge.id} | {len(plan)} books to judge first")
    if plan:
        code = _judge_plan(args, settings, store, judgments, judge, plan)
        if code != EXIT_OK or not args.yes:
            return code
    looks = 0
    if split == HELD_OUT:
        looks = len(calibration.log_held_out_look(args.sample, judge).looks)
    markdown = render_calibration(
        sample,
        split,
        judge,
        labels.by_ref(),
        judgments.judged(sample.case_set, judge),
        held_out_looks=looks,
        shared=shared,
    )
    print(markdown)
    path = calibration.root / f"{sample.version}-{judge.id}-{args.split}.md"
    write_atomic(path, markdown)
    print(f"Written to {path}")
    return EXIT_OK


def _print_parser_progress(number: int, total: int, case: ParserCase) -> None:
    print(f"[{number}/{total}] {case.id}", flush=True)


def run_config(system: System, prompt_id: str, settings: Settings) -> RunConfig:
    return RunConfig(
        system=system,
        prompt_id=prompt_id,
        model=settings.gemini_model,
        thinking_level=settings.gemini_thinking_level,
        harness=HARNESS_VERSION,
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
    if summary.replaced:
        lines.append(
            f"Replaced: {summary.replaced} infrastructure failures (old records backed up)"
        )
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
    run.add_argument(
        "--rerun-infrastructure",
        action="store_true",
        help="also rerun recorded runs an outage stopped (5xx, network, catalog), replacing "
        "them; the old records and their judgments go to data/evals/_backups (D-042)",
    )
    run.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="Goodreads export")
    run.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH, help="work key cache")
    run.add_argument("--results", type=Path, default=RESULTS_DIR, help="results directory")

    report = commands.add_parser("report", help="score the recorded runs of a case set")
    report.add_argument("--cases", default=DEFAULT_CASES, help="case set version")
    report.add_argument("--results", type=Path, default=RESULTS_DIR, help="results directory")
    report.add_argument("--judge", help="add this judge's scores, e.g. " + _DEFAULT_JUDGE_ID)

    parser_eval = commands.add_parser("parser", help="score the request interpreter on labels")
    parser_eval.add_argument("--cases", default=DEFAULT_PARSER_CASES, help="case set version")
    parser_eval.add_argument(
        "--prompt",
        choices=sorted(PARSER_PROMPTS),
        default=DEFAULT_PARSER_PROMPT.prompt_id,
        help=f"request parser prompt version (default {DEFAULT_PARSER_PROMPT.prompt_id})",
    )
    parser_eval.add_argument("--max-rpm", type=int, default=DEFAULT_MAX_RPM)
    parser_eval.add_argument("--yes", action="store_true", help="start (otherwise plan only)")
    parser_eval.add_argument("--results", type=Path, default=RESULTS_DIR)

    sample = commands.add_parser("sample", help="draw the frozen calibration sample")
    sample.add_argument("--cases", default=DEFAULT_CASES, help="case set version")
    sample.add_argument("--harness", type=int, default=JUDGED_HARNESS, help="round to sample")
    sample.add_argument("--seed", type=int, default=DEFAULT_SEED)
    sample.add_argument("--per-system", type=int, default=PER_SYSTEM)
    _calibration_arguments(sample)

    label = commands.add_parser(
        "label", help="label the calibration sample, one blind book at a time"
    )
    _calibration_arguments(label)

    relabel = commands.add_parser(
        "relabel", help="correct one tuning-half label (#N in the tuning report), with a note"
    )
    relabel.add_argument("--book", type=int, required=True, help="the book's #N")
    relabel.add_argument("--note", required=True, help="why the label is corrected")
    relabel.add_argument("--fits", choices=sorted(_VERDICTS), help="new fits_request label")
    relabel.add_argument("--quality", choices=sorted(_VERDICTS), help="new reason_quality")
    relabel.add_argument("--would-read", choices=sorted(_VERDICTS), help="new would_read")
    _calibration_arguments(relabel)

    judge = commands.add_parser("judge", help="grade every matched book of a round's records")
    judge.add_argument("--cases", default=DEFAULT_CASES, help="case set version")
    judge.add_argument("--harness", type=int, default=JUDGED_HARNESS, help="round to judge")
    judge.add_argument("--results", type=Path, default=RESULTS_DIR)
    _judge_arguments(judge)

    calibrate = commands.add_parser("calibrate", help="score the judge against your labels")
    calibrate.add_argument("--split", choices=["tuning", "held-out"], required=True)
    _calibration_arguments(calibrate)
    _judge_arguments(calibrate)
    return parser


def _calibration_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--sample", default=DEFAULT_SAMPLE, help="calibration sample version")
    command.add_argument("--calibration", type=Path, default=CALIBRATION_DIR)
    command.add_argument("--results", type=Path, default=RESULTS_DIR)


def _judge_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--prompt", choices=sorted(JUDGE_PROMPTS), default=DEFAULT_JUDGE_PROMPT.prompt_id
    )
    command.add_argument("--model", default=DEFAULT_JUDGE_MODEL, help="judge model")
    command.add_argument("--thinking", choices=["minimal", "low", "medium", "high"])
    command.add_argument("--max-rpm", type=int, default=DEFAULT_MAX_RPM)
    command.add_argument("--yes", action="store_true", help="start (otherwise plan only)")


if __name__ == "__main__":
    raise SystemExit(main())
