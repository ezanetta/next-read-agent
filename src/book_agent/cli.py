"""Command-line entry point.

    uv run python -m book_agent.cli baseline ["free-text request"] [--language en]
        [--max-pages 400] [--min-pages 200] [--csv PATH] [--cache PATH]
    uv run python -m book_agent.cli agent ["free-text request"] [same options as baseline]
        [--prompt agent_v1|agent_v2|agent_v3|agent_v4] [--verbose]
    uv run python -m book_agent.cli works [--refresh] [--csv PATH] [--cache PATH]
    uv run python -m book_agent.cli library [--refresh] [--yes] [--csv PATH] [--cache PATH]
    uv run python -m book_agent.cli export-profile --out PATH [--name NAME] [--no-covers]
        [--csv PATH]

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

library: builds the index agent_v4 retrieves from (D-041). Describes the read books once
(Open Library by cached work key, then Google Books) and embeds them. Prints a plan and
starts only with --yes; reruns continue where they stopped. --refresh describes every
book again. Run `works` first so Open Library can be used.
Exit codes: 0 index complete, 2 some books failed or errors.

export-profile: writes the library as the demo's owner profile, loaded at deploy time from
OWNER_PROFILE_PATH and never committed (D-043): only title, author, ISBNs, rating, shelf
and year, plus cover URLs (D-046): Open Library first (the edition by ISBN, then search by
title and author, one request a second), then Google Books for the books still without
one (one request each). Both are cached in the demo database, so a rerun only repeats
failures. Needs OPEN_LIBRARY_CONTACT. --no-covers skips them.
Exit codes: 0 written, 2 errors.
"""

import argparse
import asyncio
import json
import logging
import re
import sys
from collections.abc import Callable, Sequence
from contextlib import AsyncExitStack
from datetime import date
from pathlib import Path

import httpx

from book_agent.adapters.goodreads import load_goodreads_export
from book_agent.adapters.open_library import OpenLibraryCovers
from book_agent.agent import MAX_ITERATIONS, REPAIR_TURNS, AgentFailure, ToolTrace, TurnTrace
from book_agent.agent_run import (
    AGENT_PROMPTS,
    DEFAULT_AGENT_PROMPT,
    AgentRun,
    run_agent_pipeline,
)
from book_agent.api.services import utc_now
from book_agent.baseline import BaselineResult, run_baseline
from book_agent.config import Settings, get_settings
from book_agent.covers import CoverReport, Pacer, google_covers, resolve_covers
from book_agent.demo_profiles import owner_export
from book_agent.domain.acceptance import Problem, blocking_problems
from book_agent.domain.library import MIN_RATING, retrieval_pool
from book_agent.domain.models import Book, Profile
from book_agent.domain.recommendations import Check, Filters, Lookup, Unknown, ValidationResult
from book_agent.domain.requests import FilterName, ResolvedRequest
from book_agent.embedding_index import VectorStore, load_store, missing, save_store
from book_agent.files import write_atomic
from book_agent.library_enrichment import (
    EnrichmentCache,
    book_key,
    enrich_books,
    load_enrichment,
    save_enrichment,
    to_enrich,
)
from book_agent.library_retrieval import index_library, library_documents
from book_agent.llm.base import Embedder, LLMClient, LLMError
from book_agent.llm.calls import LLMCall
from book_agent.ports import (
    BookSearch,
    CoverCache,
    CoverFinder,
    LibraryRetriever,
    WorkDescriptions,
    WorkResolver,
)
from book_agent.prompts.agent_prompt import AgentPrompt
from book_agent.prompts.user_content import one_line
from book_agent.wiring import (
    DEFAULT_LIBRARY_PATHS,
    GOOGLE_BOOKS_TIMEOUT_S,
    LIBRARY_EMBEDDING_INTERVAL_S,
    NO_CONTACT,
    build_book_search,
    build_embedder,
    build_library_retriever,
    build_llm,
    build_open_library_http,
    build_work_descriptions,
    build_work_resolver,
    catalog_services,
    open_stores,
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
            if args.command in ("baseline", "agent")
            else Filters()
        )
        profile = load_goodreads_export(args.csv, profile_name="Me")
        if args.command == "export-profile" and args.no_covers:
            return _write_owner(profile, args.out, args.name, {})
        settings = get_settings()
        if args.command == "export-profile":
            return asyncio.run(_export_profile(profile, args.out, args.name, settings))
        cache = load_cache(args.cache)
        if args.command == "works":
            return asyncio.run(_works(profile, settings, cache, args.cache, refresh=args.refresh))
        if args.command == "library":
            return asyncio.run(
                _library(profile, settings, cache, refresh=args.refresh, yes=args.yes)
            )
        prompt = AGENT_PROMPTS[getattr(args, "prompt", DEFAULT_AGENT_PROMPT.prompt_id)]
        # agent_v4 needs the library index: fail before any LLM call when it's missing.
        retriever = build_library_retriever(settings, profile) if prompt.uses_retrieval else None
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
                prompt=prompt,
                retriever=retriever,
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
    retriever: LibraryRetriever | None = None,
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
                retriever=retriever,
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


async def _export_profile(profile: Profile, out: Path, name: str, settings: Settings) -> int:
    open_library = build_open_library_http(settings)
    if open_library is None:
        print(f"error: {NO_CONTACT}: covers need it (or pass --no-covers)")
        return EXIT_ERROR
    async with (
        open_stores(settings) as stores,  # the cover cache the API uses too
        open_library,
        httpx.AsyncClient(timeout=GOOGLE_BOOKS_TIMEOUT_S) as google,
    ):
        covers = await export_covers(
            profile, OpenLibraryCovers(open_library), stores.covers,
            build_book_search(settings, google),
        )  # fmt: skip
    return _write_owner(profile, out, name, covers)


async def export_covers(
    profile: Profile,
    finder: CoverFinder,
    cache: CoverCache,
    books: BookSearch,
    *,
    pacer: Pacer | None = None,
    google_pacer: Pacer | None = None,
) -> dict[int, str]:
    """The owner's covers: Open Library first (edition by ISBN, then search), then Google
    Books for the books still without one (D-046). Prints progress and a summary."""
    total = len(profile.books)
    print("Covers: Open Library, for the books the cache doesn't know, one request a second")
    first = await resolve_covers(
        profile, finder, cache, pacer=pacer, clock=utc_now, on_progress=_print_progress
    )
    print(_cover_summary("Open Library", len(first.covers), total, first))
    missing = total - len(first.covers)
    if missing == 0:
        return first.covers
    print(f"Covers: Google Books for the {missing} without one (cached: a rerun spends no quota)")
    second = await google_covers(
        profile, first.covers, books, cache,
        pacer=google_pacer, clock=utc_now, on_progress=_print_progress,
    )  # fmt: skip
    print(_cover_summary("Google Books", len(second.covers), total, second))
    return second.covers


def _cover_summary(source: str, found: int, total: int, report: CoverReport) -> str:
    failed = f", {report.failed} failed: rerun to retry them" if report.failed else ""
    return (
        f"Covers after {source}: {found} of {total} books "
        f"({report.looked_up} looked up now, {total - found} without a cover{failed})"
    )


def _write_owner(profile: Profile, out: Path, name: str, covers: dict[int, str]) -> int:
    write_atomic(out, owner_export(profile.model_copy(update={"name": name}), covers))
    print(
        f"Wrote {len(profile.books)} books to {out}: title, author, ISBNs, rating, shelf, "
        f"year, and {len(covers)} cover URLs"
    )
    print("Keep it out of the repo; point OWNER_PROFILE_PATH at it where the API runs.")
    return EXIT_OK


async def _library(
    profile: Profile, settings: Settings, work_keys: WorkKeyCache, *, refresh: bool, yes: bool
) -> int:
    paths = DEFAULT_LIBRARY_PATHS
    enrichment = load_enrichment(paths.enrichment)
    embedder = build_embedder(settings, min_interval_s=LIBRARY_EMBEDDING_INTERVAL_S)
    documents = load_store(paths.documents, embedder)
    print(format_library_plan(profile, enrichment, documents, refresh=refresh))
    if not yes:
        print("Add --yes to start.")
        return EXIT_OK
    open_library = build_open_library_http(settings)
    if open_library is None:
        print(f"Open Library is off ({NO_CONTACT}): every book goes to Google Books.")
    async with AsyncExitStack() as stack:
        http = await stack.enter_async_context(httpx.AsyncClient(timeout=GOOGLE_BOOKS_TIMEOUT_S))
        works = None
        if open_library is not None:
            works = build_work_descriptions(await stack.enter_async_context(open_library))
        output, code = await library_command(
            profile,
            enrichment,
            documents,
            work_keys,
            works=works,
            search=build_book_search(settings, http),
            embedder=embedder,
            today=date.today(),
            refresh=refresh,
            save_enrichment=lambda c: save_enrichment(c, paths.enrichment),
            save_documents=lambda store: save_store(store, paths.documents),
        )
    print(output)
    return code


def format_library_plan(
    profile: Profile, enrichment: EnrichmentCache, documents: VectorStore, *, refresh: bool
) -> str:
    pool = retrieval_pool(profile)
    todo = to_enrich(pool, enrichment, refresh=refresh)
    stale = missing(library_documents(profile, enrichment), documents)
    return "\n".join(
        [
            f"Retrieval pool: {len(pool)} read books (rated {MIN_RATING:g}+ or unrated)",
            f"To describe: {len(todo)} (Open Library first, then at most {len(todo)} "
            "Google Books searches)",
            f"To embed: {len(stale)} now, plus any book whose description changes "
            f"({documents.model}, {documents.dimensions} dimensions, 20 per request, "
            f"one request every {LIBRARY_EMBEDDING_INTERVAL_S:g} s)",
        ]
    )


async def library_command(
    profile: Profile,
    enrichment: EnrichmentCache,
    documents: VectorStore,
    work_keys: WorkKeyCache,
    *,
    works: WorkDescriptions | None,
    search: BookSearch,
    embedder: Embedder,
    today: date,
    refresh: bool = False,
    save_enrichment: Callable[[EnrichmentCache], None] | None = None,
    save_documents: Callable[[VectorStore], None] | None = None,
) -> tuple[str, int]:
    """Describe the pool books that need it, embed what changed, and report coverage."""
    pool = retrieval_pool(profile)
    summary = await enrich_books(
        to_enrich(pool, enrichment, refresh=refresh),
        enrichment,
        work_keys=work_keys,
        works=works,
        search=search,
        today=today,
        checkpoint=save_enrichment,
    )
    lines = [
        f"Described: {summary.open_library} from Open Library, {summary.google_books} from "
        f"Google Books, {summary.undescribed} with no description anywhere "
        f"({summary.search_requests} Google Books searches)",
    ]
    code = EXIT_OK
    if summary.failed:
        lines.append(f"Failed: {summary.failed} books (not cached; rerun to retry)")
        code = EXIT_ERROR
    try:
        embedded = await index_library(
            profile, enrichment, documents, embedder, checkpoint=save_documents
        )
    except LLMError as e:
        lines.append(f"error: embedding failed (finished batches are saved; rerun): {e}")
        return "\n".join(lines), EXIT_ERROR
    lines.append(f"Embedded: {len(embedded)} books")
    lines += library_coverage(profile, enrichment)
    return "\n".join(lines), code


def library_coverage(profile: Profile, enrichment: EnrichmentCache) -> list[str]:
    """How many pool books have a description: D-041 asks for 80% of the liked ones."""

    def described(books: list[Book]) -> int:
        return sum(
            1
            for b in books
            if (entry := enrichment.entries.get(book_key(b))) is not None and entry.description
        )

    pool = retrieval_pool(profile)
    liked = [b for b in pool if b in profile.liked_books]
    return [
        f"With a description: {described(pool)} of {len(pool)} pool books, "
        f"{described(liked)} of {len(liked)} liked books"
    ]


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
    retriever: LibraryRetriever | None = None,
) -> tuple[str, int]:
    """Run the agent and return the printable report and the exit code."""
    run = await run_agent_pipeline(
        profile,
        flags,
        request=request,
        llm=llm,
        books=books,
        works=works,
        prompt=prompt,
        retriever=retriever,
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
        if run.retrieved:
            lines.append("Related books shown: " + "; ".join(run.retrieved))
        if run.retrieval_error:
            lines.append(f"Retrieval failed, related books not shown: {run.retrieval_error}")
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

    export = commands.add_parser(
        "export-profile", parents=[shared], help="write the demo's owner profile (D-043)"
    )
    export.add_argument("--out", type=Path, required=True, help="where to write the JSON")
    export.add_argument("--name", default="The author", help="the profile's display name")
    export.add_argument(
        "--no-covers", action="store_true", help="skip the cover lookups (no Open Library calls)"
    )

    library = commands.add_parser(
        "library", parents=[shared], help="describe and embed the read books for agent_v4"
    )
    library.add_argument("--refresh", action="store_true", help="describe every book again")
    library.add_argument("--yes", action="store_true", help="start (otherwise: plan only)")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
