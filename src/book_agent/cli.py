"""Command-line entry point.

    uv run python -m book_agent.cli baseline ["free-text request"] [--language en]
        [--max-pages 400] [--csv PATH]

Flags override filters interpreted from the request text, field by field.
Exit codes: 0 all hard rules passed (unknowns allowed), 1 rule violations, 2 errors.
"""

import argparse
import asyncio
import re
from collections.abc import Sequence
from pathlib import Path

import httpx

from book_agent.adapters.goodreads import load_goodreads_export
from book_agent.baseline import BaselineResult, run_baseline
from book_agent.config import Settings, get_settings
from book_agent.domain.models import Profile
from book_agent.domain.recommendations import Check, Filters
from book_agent.domain.requests import FilterName, ResolvedRequest
from book_agent.llm.base import LLMClient
from book_agent.ports import BookSearch
from book_agent.wiring import build_book_search, build_llm

DEFAULT_CSV = Path("data/goodreads_library_export.csv")
EXIT_OK, EXIT_VIOLATIONS, EXIT_ERROR = 0, 1, 2

_ISO_639_1 = re.compile(r"^[a-z]{2}$")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        flags = Filters(language=args.language, max_pages=args.max_pages)
        profile = load_goodreads_export(args.csv, profile_name="Me")
        settings = get_settings()
        llm = build_llm(settings)
    except (OSError, ValueError) as e:  # missing file, bad CSV, invalid settings
        print(f"error: {e}")
        return EXIT_ERROR
    return asyncio.run(_run(profile, flags, args.request, settings, llm))


async def _run(
    profile: Profile, flags: Filters, request: str | None, settings: Settings, llm: LLMClient
) -> int:
    async with httpx.AsyncClient(timeout=15) as http:
        books = build_book_search(settings, http)
        output, code = await baseline_command(profile, flags, request, llm=llm, books=books)
    print(output)
    return code


async def baseline_command(
    profile: Profile,
    flags: Filters,
    request: str | None = None,
    *,
    llm: LLMClient,
    books: BookSearch,
) -> tuple[str, int]:
    """Run the baseline and return the printable report and the exit code."""
    result = await run_baseline(profile, flags, request=request, llm=llm, books=books)
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
    commands = parser.add_subparsers(dest="command", required=True)
    baseline = commands.add_parser("baseline", help="one recommendation call, then validation")
    baseline.add_argument(
        "request", nargs="?", help='free-text request, e.g. "a dystopia under 400 pages"'
    )
    baseline.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="Goodreads export")
    baseline.add_argument("--language", type=_language_code, help="ISO 639-1 code, e.g. en")
    baseline.add_argument("--max-pages", type=int, help="maximum page count")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
