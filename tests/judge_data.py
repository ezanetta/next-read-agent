"""Stored eval records with recommended books, for the judge and calibration tests."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime

from pydantic import HttpUrl

from book_agent.domain.models import CatalogBook
from book_agent.domain.recommendations import (
    Check,
    CheckedRecommendation,
    Filters,
    Recommendation,
    Rule,
    ValidationResult,
    Violation,
)
from book_agent.domain.requests import ResolvedRequest
from book_agent.evals.records import EvalRecord, RunConfig, profile_sha256
from book_agent.llm.base import LLMResponse
from tests.fakes import text_response
from tests.library_data import PROFILE

AGENT = RunConfig(system="agent", prompt_id="agent_v3", model="m", harness=2)
BASELINE = RunConfig(system="baseline", prompt_id="baseline_v2", model="m", harness=2)
PROFILE_SHA = profile_sha256(PROFILE)
NO_FILTERS = Filters()


def checked(
    title: str,
    *,
    matched: bool = True,
    description: str | None = "A desert planet and its politics.",
    pages: int | None = 320,
    cited: Sequence[str] = ("Dune (Dune Chronicles, #1)",),
) -> CheckedRecommendation:
    match = (
        CatalogBook(
            source_id=title.lower().replace(" ", "-"),
            title=title,
            authors=("Some Author",),
            year=1990,
            pages=pages,
            categories=("Fiction",),
            description=description,
            link=HttpUrl("https://example.com/b"),
        )
        if matched
        else None
    )
    return CheckedRecommendation(
        recommendation=Recommendation(
            title=title, author="Some Author", reason=f"Like Dune: {title}"
        ),
        match=match,
        library_match=None,
        cited_titles=list(cited),
        exists=Check.PASS if matched else Check.FAIL,
        not_in_library=Check.PASS,
        language=Check.SKIPPED,
        max_pages=Check.SKIPPED,
        cites_library=Check.PASS,
    )


def stored(
    case_id: str,
    items: Sequence[CheckedRecommendation],
    *,
    config: RunConfig = AGENT,
    run_index: int = 1,
    request: str | None = "a desert epic",
    filters: Filters = NO_FILTERS,
    violations: Sequence[tuple[Rule, int]] = (),
    error_kind: str | None = None,
) -> EvalRecord:
    resolved = ResolvedRequest(text=request, preference=request, filters=filters, sources={})
    return EvalRecord(
        case_set="set_v1",
        cases_sha256="c" * 64,
        profile_sha256=PROFILE_SHA,
        case_id=case_id,
        run_index=run_index,
        config=config,
        started_at=datetime(2026, 11, 21, tzinfo=UTC),
        duration_s=1.0,
        request=resolved,
        error_kind=error_kind,
        final_validation=ValidationResult(
            items=list(items),
            violations=[Violation(rule=r, index=i, message="x") for r, i in violations],
            unknowns=[],
        ),
    )


def verdict_reply(fits: str | None = "yes", quality: str = "partial") -> LLMResponse:
    reply: dict[str, object] = {
        "reason_quality": {"justification": "Generic but true.", "verdict": quality}
    }
    if fits is not None:
        reply["fits_request"] = {"justification": "A desert epic.", "verdict": fits}
    return text_response(json.dumps(reply))
