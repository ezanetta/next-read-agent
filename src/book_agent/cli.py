"""Command-line entry point.

    uv run python -m book_agent.cli baseline ["free-text request"] [--language en]
        [--max-pages 400] [--min-pages 200] [--csv PATH] [--cache PATH]
    uv run python -m book_agent.cli agent ["free-text request"] [same options as baseline]
        [--prompt agent_v1|agent_v2|agent_v3] [--verbose]
    uv run python -m book_agent.cli works [--refresh] [--csv PATH] [--cache PATH]

baseline: flags override filters interpreted from the request text, field by field.
Exit codes: 0 all hard rules passed (unknowns allowed), 1 rule violations, 2 errors.

agent: same request handling and filters as baseline; reports iterations, submissions,
each rejection with its reasons and every LLM call. --verbose adds a trace of each turn
(tool calls with their arguments and a one-line outcome) and debug logs.
Exit codes: 0 accepted, 1 only rejected submissions until the iterations ran out, 2 errors
(failed interpretation, LLM error, catalog unreachable, token limit).

works: resolves the library's ISBNs to Open Library work keys once and caches them, so
baseline runs don't repeat the requests. --refresh looks every ISBN up again.
Exit codes: 0 every lookup done, 2 some lookups failed or errors.
"""

import argparse
import asyncio
import json
import logging
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from book_agent.adapters.goodreads import load_goodreads_export
from book_agent.agent import MAX_ITERATIONS, REPAIR_TURNS, AgentFailure, ToolTrace, TurnTrace
from book_agent.agent_run import (
    AGENT_PROMPTS,
    DEFAULT_AGENT_PROMPT,
    AgentRun,
    run_agent_pipeline,
)
from book_agent.baseline import BaselineResult, run_baseline
from book_agent.config import Settings, get_settings
from book_agent.domain.acceptance import Problem, blocking_problems
from book_agent.domain.models import Profile
from book_agent.domain.recommendations import Check, Filters, Lookup, Unknown, ValidationResult
from book_agent.domain.requests import FilterName, ResolvedRequest
from book_agent.llm.base import LLMClient
from book_agent.llm.calls import LLMCall
from book_agent.ports import BookSearch, WorkResolver
from book_agent.prompts.agent_prompt import AgentPrompt
from book_agent.prompts.user_content import one_line
from book_agent.wiring import (
    NO_CONTACT,
    build_llm,
    build_open_library_http,
    build_work_resolver,
    catalog_services,
)
from book_agent.work_keys import (
    DEFAULT_CACHE_PATH,
    CachedWorkResolver,
    Checkpoint,
    Progress,
    WorkKeyCache,
    format_coverage,
    load_cache,
    resolve_library,
    save_cache,
)

DEFAULT_CSV = Path("data/goodreads_library_export.csv")
EXIT_OK, EXIT_VIOLATIONS, EXIT_ERROR = 0, 1, 2

_ISO_639_1 = re.compile(r"^[a-z]{2}$")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    verbose: bool = getattr(args, "verbose", False)
    _configure_logging(verbose)
    try:
        # Flags first: a contradictory combination fails before any file is read.
        flags = (
            Filters(language=args.language, max_pages=args.max_pages, min_pages=args.min_pages)
            if args.command != "works"
            else Filters()
        )
        profile = load_goodreads_export(args.csv, profile_name="Me")
        settings = get_settings()
        cache = load_cache(args.cache)
        if args.command == "works":
            return asyncio.run(_works(profile, settings, cache, args.cache, refresh=args.refresh))
        llm = build_llm(settings)
        return asyncio.run(
            _run(
                args.command,
                profile,
                flags,
                args.request,
                settings,
                llm,
                cache,
                args.cache,
                verbose=verbose,
                prompt=AGENT_PROMPTS[getattr(args, "prompt", DEFAULT_AGENT_PROMPT.prompt_id)],
            )
        )
    # Missing file, bad CSV or cache, invalid settings, contradictory flags.
    except (OSError, ValueError) as e:
        print(f"error: {e}")
        return EXIT_ERROR


async def _run(
    command: str,
    profile: Profile,
    flags: Filters,
    request: str | None,
    settings: Settings,
    llm: LLMClient,
    cache: WorkKeyCache,
    cache_path: Path,
    *,
    verbose: bool = False,
    prompt: AgentPrompt = DEFAULT_AGENT_PROMPT,
) -> int:
    async with catalog_services(settings, profile, cache, cache_path) as services:
        books, works = services.books, services.works
        if command == "agent":
            output, code = await agent_command(
                profile,
                flags,
                request,
                llm=llm,
                books=books,
                works=works,
                verbose=verbose,
                prompt=prompt,
            )
        else:
            output, code = await baseline_command(
                profile, flags, request, llm=llm, books=books, works=works
            )
    print("\n".join([*services.notes, output]))
    return code


async def _works(
    profile: Profile, settings: Settings, cache: WorkKeyCache, cache_path: Path, *, refresh: bool
) -> int:
    open_library = build_open_library_http(settings)
    if open_library is None:
        print(f"error: {NO_CONTACT}; Open Library asks clients for contact details")
        return EXIT_ERROR
    resolver = CachedWorkResolver(build_work_resolver(open_library), cache, refresh=refresh)
    try:
        async with open_library:
            output, code = await works_command(
                profile,
                resolver,
                progress=_print_progress,
                checkpoint=lambda c: save_cache(c, cache_path),
            )
    finally:  # also on Ctrl-C: keep every lookup made so far
        if resolver.changed:
            save_cache(resolver.cache, cache_path)
    print(output)
    return code


async def works_command(
    profile: Profile,
    resolver: CachedWorkResolver,
    *,
    progress: Progress | None = None,
    checkpoint: Checkpoint | None = None,
) -> tuple[str, int]:
    """Resolve the library's ISBNs and return the coverage report and the exit code."""
    library, failed = await resolve_library(
        profile, resolver, progress=progress, checkpoint=checkpoint
    )
    return "\n".join(format_coverage(library, failed)), EXIT_ERROR if failed else EXIT_OK


def format_progress(done: int, total: int) -> str:
    return f"Resolving work keys: {done} of {total}"


def _print_progress(done: int, total: int) -> None:
    """One updating line on a terminal; every 20 lookups and the last one otherwise."""
    if sys.stdout.isatty():
        print(f"\r{format_progress(done, total)}", end="\n" if done == total else "", flush=True)
    elif done % 20 == 0 or done == total:
        print(format_progress(done, total), flush=True)


async def baseline_command(
    profile: Profile,
    flags: Filters,
    request: str | None = None,
    *,
    llm: LLMClient,
    books: BookSearch,
    works: WorkResolver | None = None,
) -> tuple[str, int]:
    """Run the baseline and return the printable report and the exit code."""
    result = await run_baseline(profile, flags, request=request, llm=llm, books=books, works=works)
    return format_result(result), exit_code(result)


async def agent_command(
    profile: Profile,
    flags: Filters,
    request: str | None = None,
    *,
    llm: LLMClient,
    books: BookSearch,
    works: WorkResolver | None = None,
    verbose: bool = False,
    prompt: AgentPrompt = DEFAULT_AGENT_PROMPT,
) -> tuple[str, int]:
    """Run the agent and return the printable report and the exit code."""
    run = await run_agent_pipeline(
        profile, flags, request=request, llm=llm, books=books, works=works, prompt=prompt
    )
    return format_agent_run(run, verbose=verbose), agent_exit_code(run)


def exit_code(result: BaselineResult) -> int:
    if result.error is not None or result.validation is None:
        return EXIT_ERROR
    return EXIT_OK if result.validation.passed else EXIT_VIOLATIONS


def format_result(result: BaselineResult) -> str:
    prompts = f"Prompt {result.prompt_id}"
    if result.request_prompt_id:
        prompts += f" | request parser {result.request_prompt_id}"
    lines = _header(prompts, result.request_text, result.request)

    if result.error is not None:
        lines.append(f"ERROR: {result.error}")
        if result.raw_text:
            lines += ["", "Raw reply:", result.raw_text]
    elif result.validation is not None:
        lines += _recommendations(result.validation)

    lines.append("")
    lines += _calls(result.calls)
    if result.validation_latency_s is not None:
        lines.append(f"Validation: {result.validation_latency_s:.2f}s")
    return "\n".join(lines)


def agent_exit_code(run: AgentRun) -> int:
    """0 accepted; 1 only rejected submissions until the iterations ran out; 2 errors."""
    if run.passed:
        return EXIT_OK
    if run.failure is AgentFailure.ITERATIONS_EXHAUSTED and run.rejections:
        return EXIT_VIOLATIONS
    return EXIT_ERROR


def format_agent_run(run: AgentRun, *, verbose: bool = False) -> str:
    prompts = f"Prompt {run.prompt_id} | feedback {run.feedback_prompt_id}"
    if run.request_prompt_id:
        prompts += f" | request parser {run.request_prompt_id}"
    lines = _header(prompts, run.request_text, run.request)

    if run.request is not None:
        rejected = f" ({len(run.rejections)} rejected)" if run.rejections else ""
        lines.append(
            f"Iterations: {run.iterations} of {MAX_ITERATIONS}"
            f"{f' + {REPAIR_TURNS} repair turn' if run.repair_turn_used else ''} | "
            f"Submissions: {run.submissions}{rejected}"
        )
        lines.append("")
    if verbose and run.turns:
        lines += [*format_trace(run.turns), ""]
    for number, rejection in enumerate(run.rejections, start=1):
        lines.append(f"Rejection {number}:")
        lines += [f"- {_problem(p)}" for p in blocking_problems(rejection)]
        lines.append("")

    if run.error is not None:
        kind = f" ({run.failure.value})" if run.failure else ""
        lines.append(f"ERROR{kind}: {run.error}")
        if run.raw_text:
            lines += ["", "Raw reply:", run.raw_text]
        if run.last_validation is not None and run.last_validation not in run.rejections:
            lines += ["", "Last submission:"]
            lines += _problems(run.last_validation)
    elif run.validation is not None:
        lines += _recommendations(run.validation)

    lines.append("")
    lines += _calls(run.calls)
    if (usage := run.total_usage) is not None:
        lines.append(f"Total: {usage.input_tokens} in, {usage.output_tokens} out")
    return "\n".join(lines)


def format_trace(turns: list[TurnTrace]) -> list[str]:
    """One heading line per turn, then one line per tool call: arguments -> outcome."""
    lines = ["Trace:"]
    for turn in turns:
        forced = f", forced {turn.forced_tool}" if turn.forced_tool else ""
        retried = ", retried after a server error" if turn.llm_retries else ""
        heading = (
            f"Turn {turn.iteration} ({turn.stop_reason.value}, {turn.output_tokens} out"
            f"{forced}{retried})"
        )
        lines.append(f"{heading}: {turn.note}" if turn.note else heading)
        if turn.text.strip():
            lines.append(f'  text: "{_shorten(one_line(turn.text), 120)}"')
        for tool in turn.tools:
            error = "[error] " if tool.is_error else ""
            lines.append(f"  {tool.name} {_arguments(tool)} -> {error}{tool.outcome}")
        if turn.submit_notice:
            lines.append("  (submit-now notice sent)")
        if turn.repair_turn_granted:
            lines.append("  (repair turn granted)")
    return lines


def _arguments(tool: ToolTrace) -> str:
    """Search arguments as compact JSON; a submission as its titles, not three reasons."""
    recommendations = tool.arguments.get("recommendations")
    if tool.name == "submit_recommendations" and isinstance(recommendations, list):
        titles = [
            str(item.get("title", "?")) if isinstance(item, dict) else "?"
            for item in recommendations
        ]
        return "[" + "; ".join(titles) + "]"
    return _shorten(json.dumps(tool.arguments, ensure_ascii=False), 120)


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _configure_logging(verbose: bool) -> None:
    """Warnings always; with --verbose, also this app's debug lines (e.g. skipped volumes)."""
    logging.basicConfig(format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("book_agent").setLevel(logging.DEBUG if verbose else logging.WARNING)


def _header(prompts: str, request_text: str | None, request: ResolvedRequest | None) -> list[str]:
    lines = [prompts]
    if request_text:
        lines.append(f'Request: "{request_text}"')
    if request is not None:
        lines += _understood(request)
    lines.append("")
    return lines


def _calls(calls: list[LLMCall]) -> list[str]:
    if not calls:
        return []
    lines = ["LLM calls:"]
    for call in calls:
        tokens = (
            f"{call.usage.input_tokens} in, {call.usage.output_tokens} out"
            if call.usage
            else "no usage"
        )
        model = call.model or "failed"
        retried = " | retried once" if call.retries else ""
        lines.append(f"- {call.name}: {model} | {tokens} | {call.latency_s:.2f}s{retried}")
    return lines


def _problem(problem: Problem) -> str:
    unknown = " (unknown)" if isinstance(problem, Unknown) else ""
    return f"[{problem.rule.value}{unknown}] {problem.message}"


def _understood(request: ResolvedRequest) -> list[str]:
    lines: list[str] = []
    if request.preference:
        lines.append(f"Preference: {request.preference}")
    filters: list[str] = []
    if request.filters.language:
        filters.append(f"language={request.filters.language} ({_source(request, 'language')})")
    if request.filters.max_pages:
        filters.append(f"max_pages={request.filters.max_pages} ({_source(request, 'max_pages')})")
    if request.filters.min_pages:
        filters.append(f"min_pages={request.filters.min_pages} ({_source(request, 'min_pages')})")
    if filters:
        lines.append("Filters: " + ", ".join(filters))
    elif request.text:
        lines.append("Filters: none")
    return lines


def _source(request: ResolvedRequest, name: FilterName) -> str:
    source = request.sources.get(name)
    return f"from {source.value}" if source else "source unknown"


def _recommendations(validation: ValidationResult) -> list[str]:
    lines: list[str] = []
    for number, item in enumerate(validation.items, start=1):
        rec, match = item.recommendation, item.match
        lines.append(f"{number}. {rec.title} by {rec.author}")
        lines.append(f"   Reason: {rec.reason}")
        if match is not None:
            via = " (found by the fallback lookup)" if item.found_by is Lookup.FALLBACK else ""
            lines.append(f"   Matched: {match.title} | {match.link}{via}")
            pages = match.pages or "unknown"
            lines.append(f"   Pages: {pages} | Language: {match.language or 'unknown'}")
        checks = {
            "exists": item.exists,
            "not in library": item.not_in_library,
            "language": item.language,
            "max pages": item.max_pages,
            "min pages": item.min_pages,
            "cites history": item.cites_library,
        }
        lines.append(
            "   Checks: "
            + ", ".join(f"{name}={c.value}" for name, c in checks.items() if c != Check.SKIPPED)
        )
        lines.append("")

    lines += _problems(validation)
    if not validation.passed:
        verdict = "FAILED"
    elif validation.fully_verified:
        verdict = "PASSED"
    else:
        verdict = "PASSED WITH UNKNOWNS"
    lines.append(f"Result: {verdict}")
    return lines


def _problems(validation: ValidationResult) -> list[str]:
    lines: list[str] = []
    if validation.violations:
        lines.append("Violations:")
        lines += [f"- [{v.rule.value}] {v.message}" for v in validation.violations]
    if validation.unknowns:
        lines.append("Unknowns:")
        lines += [f"- [{u.rule.value}] {u.message}" for u in validation.unknowns]
    return lines


def _language_code(value: str) -> str:
    code = value.strip().casefold()
    if not _ISO_639_1.match(code):
        raise argparse.ArgumentTypeError(
            f"invalid language {value!r}: use a two-letter ISO 639-1 code, e.g. en"
        )
    return code


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m book_agent.cli")
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="Goodreads export")
    shared.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH, help="work key cache")

    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("baseline", "one recommendation call, then validation"),
        ("agent", "the agent: searches, submits, gets rejections back, resubmits"),
    ):
        command = commands.add_parser(name, parents=[shared], help=help_text)
        command.add_argument(
            "request", nargs="?", help='free-text request, e.g. "a dystopia under 400 pages"'
        )
        command.add_argument("--language", type=_language_code, help="ISO 639-1 code, e.g. en")
        command.add_argument("--max-pages", type=int, help="maximum page count")
        command.add_argument("--min-pages", type=int, help="minimum page count")
        if name == "agent":
            command.add_argument(
                "--prompt",
                choices=sorted(AGENT_PROMPTS),
                default=DEFAULT_AGENT_PROMPT.prompt_id,
                help=f"agent prompt version (default {DEFAULT_AGENT_PROMPT.prompt_id})",
            )
            command.add_argument(
                "-v",
                "--verbose",
                action="store_true",
                help="print each turn's tool calls and outcomes, and debug logs",
            )

    works = commands.add_parser(
        "works", parents=[shared], help="resolve the library to work keys and cache them"
    )
    works.add_argument("--refresh", action="store_true", help="look up every ISBN again")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
