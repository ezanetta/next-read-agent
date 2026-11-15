"""Command-line entry point.

    uv run python -m book_agent.cli baseline [--language en] [--max-pages 400] [--csv PATH]

Exit codes: 0 all hard rules passed (unknowns allowed), 1 rule violations, 2 errors.
"""

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

import httpx

from book_agent.adapters.goodreads import load_goodreads_export
from book_agent.baseline import BaselineResult, run_baseline
from book_agent.config import Settings, get_settings
from book_agent.domain.models import Profile
from book_agent.domain.recommendations import Check, Filters
from book_agent.llm.base import LLMClient
from book_agent.ports import BookSearch
from book_agent.wiring import build_book_search, build_llm

DEFAULT_CSV = Path("data/goodreads_library_export.csv")
EXIT_OK, EXIT_VIOLATIONS, EXIT_ERROR = 0, 1, 2


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        filters = Filters(language=args.language, max_pages=args.max_pages)
        profile = load_goodreads_export(args.csv, profile_name="Me")
        settings = get_settings()
        llm = build_llm(settings)
    except (OSError, ValueError) as e:  # missing file, bad CSV, invalid settings or filters
        print(f"error: {e}")
        return EXIT_ERROR
    return asyncio.run(_run(profile, filters, settings, llm))


async def _run(profile: Profile, filters: Filters, settings: Settings, llm: LLMClient) -> int:
    async with httpx.AsyncClient(timeout=15) as http:
        books = build_book_search(settings, http)
        output, code = await baseline_command(profile, filters, llm=llm, books=books)
    print(output)
    return code


async def baseline_command(
    profile: Profile, filters: Filters, *, llm: LLMClient, books: BookSearch
) -> tuple[str, int]:
    """Run the baseline and return the printable report and the exit code."""
    result = await run_baseline(profile, filters, llm=llm, books=books)
    return format_result(result, filters), exit_code(result)


def exit_code(result: BaselineResult) -> int:
    if result.error is not None or result.validation is None:
        return EXIT_ERROR
    return EXIT_OK if result.validation.passed else EXIT_VIOLATIONS


def format_result(result: BaselineResult, filters: Filters) -> str:
    lines = [f"Prompt {result.prompt_id} | model {result.model or 'unknown'}"]
    if filters.language or filters.max_pages:
        lines.append(f"Filters: language={filters.language} max_pages={filters.max_pages}")
    lines.append("")

    if result.error is not None:
        lines.append(f"ERROR: {result.error}")
        if result.raw_text:
            lines += ["", "Raw reply:", result.raw_text]
    elif result.validation is not None:
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

    lines.append("")
    if result.usage is not None:
        lines.append(f"Tokens: {result.usage.input_tokens} in, {result.usage.output_tokens} out")
    latency = f"Latency: LLM {result.llm_latency_s:.2f}s"
    if result.validation_latency_s is not None:
        latency += f", validation {result.validation_latency_s:.2f}s"
    lines.append(latency)
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m book_agent.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    baseline = commands.add_parser("baseline", help="one LLM call, no tools, then validation")
    baseline.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="Goodreads export")
    baseline.add_argument("--language", help="ISO 639-1 code, e.g. en")
    baseline.add_argument("--max-pages", type=int, help="maximum page count")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
