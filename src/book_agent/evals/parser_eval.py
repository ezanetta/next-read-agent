"""The request parser eval: one interpretation per labeled request, scored per field.

Fields: preference presence, language, max_pages, and the terms that must survive in
the preference (matched ignoring case and accents). A dropped term is reported apart
from filter errors. Known blind spot: term matching can't see a lost negation
("nothing from Sanderson" -> "Sanderson"), so the report prints every interpreted
preference for a human to read; the phase-2 judge will cover it (D-025).

Results are saved after every case, one file per configuration, so a quota stop
loses nothing and a rerun resumes.
"""

import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from book_agent.evals.cases import LoadedCases, ParserCase, ParserCaseSet
from book_agent.evals.pacing import QuotaExhausted
from book_agent.files import write_atomic
from book_agent.llm.base import LLMClient
from book_agent.prompts import request_parser_v1
from book_agent.request_parser import interpret_request


class ParserOutcome(BaseModel, frozen=True):
    """What the interpreter returned for one case."""

    case_id: str
    preference: str | None = None
    language: str | None = None
    max_pages: int | None = None
    error: str | None = None
    raw_text: str | None = None


class ParserRecords(BaseModel):
    """All outcomes of one configuration on one case set."""

    case_set: str
    cases_sha256: str
    prompt_id: str
    model: str
    thinking_level: str | None = None
    git_commit: str | None = None
    started_at: datetime
    outcomes: list[ParserOutcome] = []


class ParserScore(BaseModel, frozen=True):
    case: ParserCase
    outcome: ParserOutcome
    preference_ok: bool
    language_ok: bool
    max_pages_ok: bool
    dropped_terms: list[str]

    @property
    def filters_ok(self) -> bool:
        return self.preference_ok and self.language_ok and self.max_pages_ok

    @property
    def passed(self) -> bool:
        return self.outcome.error is None and self.filters_ok and not self.dropped_terms


def fold(text: str) -> str:
    """Casefolded, accents removed: "Distopía" and "distopia" compare equal."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def score(case: ParserCase, outcome: ParserOutcome) -> ParserScore:
    preference = fold(outcome.preference or "")
    return ParserScore(
        case=case,
        outcome=outcome,
        preference_ok=outcome.error is None
        and (outcome.preference is not None) == case.has_preference,
        language_ok=outcome.error is None and outcome.language == case.language,
        max_pages_ok=outcome.error is None and outcome.max_pages == case.max_pages,
        dropped_terms=[t for t in case.preference_contains if fold(t) not in preference],
    )


def records_path(results: Path, case_set: str, model: str, thinking: str | None) -> Path:
    name = f"{request_parser_v1.PROMPT_ID}-{model}-{thinking or 'default'}.json"
    return results / case_set / name.replace("/", "_")


def load_records(path: Path) -> ParserRecords | None:
    return ParserRecords.model_validate_json(path.read_bytes()) if path.exists() else None


def pending(loaded: LoadedCases[ParserCaseSet], records: ParserRecords | None) -> list[ParserCase]:
    """Cases without an outcome yet. Raises ValueError if the case file changed (D-023)."""
    if records is not None and records.cases_sha256 != loaded.sha256:
        raise ValueError(
            f"{loaded.cases.version} changed after it had results; it is frozen. "
            "Put the changes in a new version."
        )
    done = {o.case_id for o in records.outcomes} if records else set[str]()
    return [c for c in loaded.cases.cases if c.id not in done]


async def run_parser_eval(
    cases: list[ParserCase],
    records: ParserRecords,
    path: Path,
    *,
    llm: LLMClient,
    progress: Callable[[int, int, ParserCase], None] | None = None,
) -> str | None:
    """Interpret each case once and save after each. Returns the stop reason, if any."""
    for number, case in enumerate(cases, start=1):
        if progress is not None:
            progress(number, len(cases), case)
        try:
            interpretation = await interpret_request(case.request, llm=llm)
        except QuotaExhausted as e:
            return str(e)
        parsed = interpretation.parsed
        records.outcomes.append(
            ParserOutcome(
                case_id=case.id,
                preference=parsed.preference if parsed else None,
                language=parsed.filters.language if parsed else None,
                max_pages=parsed.filters.max_pages if parsed else None,
                error=interpretation.error,
                raw_text=interpretation.raw_text,
            )
        )
        write_atomic(path, records.model_dump_json(indent=1) + "\n")
    return None


def new_records(
    loaded: LoadedCases[ParserCaseSet], model: str, thinking: str | None, git_commit: str | None
) -> ParserRecords:
    return ParserRecords(
        case_set=loaded.cases.version,
        cases_sha256=loaded.sha256,
        prompt_id=request_parser_v1.PROMPT_ID,
        model=model,
        thinking_level=thinking,
        git_commit=git_commit,
        started_at=datetime.now(UTC),
    )


def scores(loaded: LoadedCases[ParserCaseSet], records: ParserRecords) -> list[ParserScore]:
    """Scores for the cases that have an outcome, in case-file order."""
    outcomes = {o.case_id: o for o in records.outcomes}
    return [score(c, outcomes[c.id]) for c in loaded.cases.cases if c.id in outcomes]


def render_parser_report(records: ParserRecords, results: list[ParserScore]) -> str:
    total = len(results)
    with_terms = [s for s in results if s.case.preference_contains]
    kept = sum(not s.dropped_terms for s in with_terms)
    lines = [
        f"# Request parser eval: {records.case_set}",
        "",
        f"Prompt {records.prompt_id} | model {records.model} "
        f"| thinking {records.thinking_level or 'default'} | {total} cases",
        "",
        "| Field | Correct |",
        "| --- | --- |",
        f"| preference present | {sum(s.preference_ok for s in results)}/{total} |",
        f"| language | {sum(s.language_ok for s in results)}/{total} |",
        f"| max_pages | {sum(s.max_pages_ok for s in results)}/{total} |",
        f"| preference terms kept | {kept}/{len(with_terms)} cases |",
        f"| all fields | {sum(s.passed for s in results)}/{total} |",
        "",
    ]
    errors = [s for s in results if s.outcome.error]
    filter_mismatches = [s for s in results if not s.outcome.error and not s.filters_ok]
    dropped = [s for s in results if s.dropped_terms]
    lines += ["## Errors", ""] + (
        [f"- {s.case.id}: {s.outcome.error} (raw: {s.outcome.raw_text!r})" for s in errors]
        or ["None."]
    )
    lines += ["", "## Filter mismatches", ""]
    lines += [line for s in filter_mismatches for line in _filter_lines(s)] or ["None."]
    lines += ["", "## Dropped preference terms", ""]
    lines += [
        f"- {s.case.id}: {', '.join(repr(t) for t in s.dropped_terms)} missing from "
        f"{s.outcome.preference!r}"
        for s in dropped
    ] or ["None."]
    lines += ["", "## Every interpreted preference (read for lost negations)", ""]
    for s in results:
        mark = "ok" if s.passed else "MISMATCH"
        lines.append(f"- {s.case.id} [{mark}]: {s.case.request!r} -> {s.outcome.preference!r}")
    return "\n".join(lines) + "\n"


def _filter_lines(s: ParserScore) -> list[str]:
    lines: list[str] = []
    if not s.preference_ok:
        expected = "a preference" if s.case.has_preference else "no preference"
        lines.append(f"- {s.case.id}: expected {expected}, got {s.outcome.preference!r}")
    if not s.language_ok:
        lines.append(
            f"- {s.case.id}: language expected {s.case.language!r}, got {s.outcome.language!r}"
        )
    if not s.max_pages_ok:
        lines.append(
            f"- {s.case.id}: max_pages expected {s.case.max_pages!r}, got {s.outcome.max_pages!r}"
        )
    return lines
