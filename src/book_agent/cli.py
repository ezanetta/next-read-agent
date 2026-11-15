"""Command-line entry point.

    uv run python -m book_agent.cli baseline ["free-text request"] [--language en]
        [--max-pages 400] [--csv PATH] [--cache PATH]
    uv run python -m book_agent.cli works [--refresh] [--csv PATH] [--cache PATH]

baseline: flags override filters interpreted from the request text, field by field.
Exit codes: 0 all hard rules passed (unknowns allowed), 1 rule violations, 2 errors.

works: resolves the library's ISBNs to Open Library work keys once and caches them, so
baseline runs don't repeat the requests. --refresh looks every ISBN up again.
Exit codes: 0 every lookup done, 2 some lookups failed or errors.
"""

import argparse
import asyncio
import re
import sys
from collections.abc import Sequence
from contextlib import AsyncExitStack
from pathlib import Path

import httpx

from book_agent.adapters.goodreads import load_goodreads_export
from book_agent.baseline import BaselineResult, run_baseline
from book_agent.config import Settings, get_settings
from book_agent.domain.models import Profile
from book_agent.domain.recommendations import Check, Filters
from book_agent.domain.requests import FilterName, ResolvedRequest
from book_agent.llm.base import LLMClient
from book_agent.ports import BookSearch, WorkResolver
from book_agent.wiring import (
    build_book_search,
    build_llm,
    build_open_library_http,
    build_work_resolver,
)
from book_agent.work_keys import (
    DEFAULT_CACHE_PATH,
    CachedWorkResolver,
    Checkpoint,
    Progress,
    WorkKeyCache,
    coverage,
    format_coverage,
    load_cache,
    resolve_library,
    save_cache,
)

DEFAULT_CSV = Path("data/goodreads_library_export.csv")
NO_CONTACT = "OPEN_LIBRARY_CONTACT is not set in .env"
EXIT_OK, EXIT_VIOLATIONS, EXIT_ERROR = 0, 1, 2

_ISO_639_1 = re.compile(r"^[a-z]{2}$")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        profile = load_goodreads_export(args.csv, profile_name="Me")
        settings = get_settings()
        cache = load_cache(args.cache)
        if args.command == "works":
            return asyncio.run(_works(profile, settings, cache, args.cache, refresh=args.refresh))
        flags = Filters(language=args.language, max_pages=args.max_pages)
        llm = build_llm(settings)
        return asyncio.run(_run(profile, flags, args.request, settings, llm, cache, args.cache))
    except (OSError, ValueError) as e:  # missing file, bad CSV or cache, invalid settings
        print(f"error: {e}")
        return EXIT_ERROR


async def _run(
    profile: Profile,
    flags: Filters,
    request: str | None,
    settings: Settings,
    llm: LLMClient,
    cache: WorkKeyCache,
    cache_path: Path,
) -> int:
    notes: list[str] = []
    async with AsyncExitStack() as stack:
        http = await stack.enter_async_context(httpx.AsyncClient(timeout=15))
        books = build_book_search(settings, http)
        works: CachedWorkResolver | None = None
        open_library = build_open_library_http(settings)
        if open_library is None:
            notes.append(f"Work-key matching is off: {NO_CONTACT}.")
        else:
            await stack.enter_async_context(open_library)
            works = CachedWorkResolver(build_work_resolver(open_library), cache)
            library = coverage(profile, cache)
            if not library.complete:
                notes.append(
                    f"Work keys: {library.unchecked} library ISBNs not resolved yet; "
                    "run the works command first."
                )
        output, code = await baseline_command(
            profile, flags, request, llm=llm, books=books, works=works
        )
        if works is not None and works.changed:
            save_cache(works.cache, cache_path)
    print("\n".join([*notes, output]))
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


def exit_code(result: BaselineResult) -> int:
    if result.error is not None or result.validation is None:
        return EXIT_ERROR
    return EXIT_OK if result.validation.passed else EXIT_VIOLATIONS


def format_result(result: BaselineResult) -> str:
    prompts = f"Prompt {result.prompt_id}"
    if result.request_prompt_id:
        prompts += f" | request parser {result.request_prompt_id}"
    lines = [prompts]
    if result.request_text:
        lines.append(f'Request: "{result.request_text}"')
    if result.request is not None:
        lines += _understood(result.request)
    lines.append("")

    if result.error is not None:
        lines.append(f"ERROR: {result.error}")
        if result.raw_text:
            lines += ["", "Raw reply:", result.raw_text]
    elif result.validation is not None:
        lines += _recommendations(result)

    lines.append("")
    if result.calls:
        lines.append("LLM calls:")
        for call in result.calls:
            tokens = (
                f"{call.usage.input_tokens} in, {call.usage.output_tokens} out"
                if call.usage
                else "no usage"
            )
            model = call.model or "failed"
            lines.append(f"- {call.name}: {model} | {tokens} | {call.latency_s:.2f}s")
    if result.validation_latency_s is not None:
        lines.append(f"Validation: {result.validation_latency_s:.2f}s")
    return "\n".join(lines)


def _understood(request: ResolvedRequest) -> list[str]:
    lines: list[str] = []
    if request.preference:
        lines.append(f"Preference: {request.preference}")
    filters: list[str] = []
    if request.filters.language:
        filters.append(f"language={request.filters.language} ({_source(request, 'language')})")
    if request.filters.max_pages:
        filters.append(f"max_pages={request.filters.max_pages} ({_source(request, 'max_pages')})")
    if filters:
        lines.append("Filters: " + ", ".join(filters))
    elif request.text:
        lines.append("Filters: none")
    return lines


def _source(request: ResolvedRequest, name: FilterName) -> str:
    source = request.sources.get(name)
    return f"from {source.value}" if source else "source unknown"


def _recommendations(result: BaselineResult) -> list[str]:
    assert result.validation is not None
    lines: list[str] = []
    for number, item in enumerate(result.validation.items, start=1):
        rec, match = item.recommendation, item.match
        lines.append(f"{number}. {rec.title} by {rec.author}")
        lines.append(f"   Reason: {rec.reason}")
        if match is not None:
            lines.append(f"   Matched: {match.title} | {match.link}")
            pages = match.pages or "unknown"
            lines.append(f"   Pages: {pages} | Language: {match.language or 'unknown'}")
        checks = {
            "exists": item.exists,
            "not in library": item.not_in_library,
            "language": item.language,
            "max pages": item.max_pages,
            "cites history": item.cites_library,
        }
        lines.append(
            "   Checks: "
            + ", ".join(f"{name}={c.value}" for name, c in checks.items() if c != Check.SKIPPED)
        )
        lines.append("")

    validation = result.validation
    if validation.violations:
        lines.append("Violations:")
        lines += [f"- [{v.rule.value}] {v.message}" for v in validation.violations]
    if validation.unknowns:
        lines.append("Unknowns:")
        lines += [f"- [{u.rule.value}] {u.message}" for u in validation.unknowns]
    if not validation.passed:
        verdict = "FAILED"
    elif validation.fully_verified:
        verdict = "PASSED"
    else:
        verdict = "PASSED WITH UNKNOWNS"
    lines.append(f"Result: {verdict}")
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
    baseline = commands.add_parser(
        "baseline", parents=[shared], help="one recommendation call, then validation"
    )
    baseline.add_argument(
        "request", nargs="?", help='free-text request, e.g. "a dystopia under 400 pages"'
    )
    baseline.add_argument("--language", type=_language_code, help="ISO 639-1 code, e.g. en")
    baseline.add_argument("--max-pages", type=int, help="maximum page count")

    works = commands.add_parser(
        "works", parents=[shared], help="resolve the library to work keys and cache them"
    )
    works.add_argument("--refresh", action="store_true", help="look up every ISBN again")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
