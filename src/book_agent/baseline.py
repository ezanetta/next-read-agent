"""The no-tools baseline: one recommendation call with the whole profile, then validation.

With a free-text request, a separate short call interprets it first (request_parser), and
its filters are merged with the explicit ones. Validation includes the LLM same-work
check for recommendations by authors already in the library. The simplest end-to-end run
of the project and the first baseline for evals. run_baseline never raises for model or
parsing problems: they end up in result.error, so an eval run always gets one result per case.
"""

from time import perf_counter
from typing import cast

from pydantic import BaseModel

from book_agent.domain.models import Profile
from book_agent.domain.recommendations import Filters, RecommendationList, ValidationResult
from book_agent.domain.requests import ResolvedRequest, merge_filters
from book_agent.domain.validation import WorkMatching, validate_recommendations
from book_agent.json_reply import ReplyParseError, extract_json, parse_model
from book_agent.llm.base import LLMClient, LLMError, Message, StopReason, Usage
from book_agent.llm.calls import LLMCall, total_usage
from book_agent.ports import BookSearch, WorkResolver
from book_agent.prompts import baseline_v1
from book_agent.request_parser import interpret_request
from book_agent.same_work_judge import LLMSameWorkJudge

CALL_NAME = "recommend"
DEFAULT_MAX_TOKENS = 2048


class RecommendationParseError(ReplyParseError):
    """The model's reply could not be read as a recommendation list."""


class BaselineResult(BaseModel, frozen=True):
    prompt_id: str
    request_prompt_id: str | None = None  # set when a request text was interpreted
    request_text: str | None = None
    request: ResolvedRequest | None = None  # None when interpretation failed
    # In order: interpret_request (optional), recommend, then one judge_same_work per
    # recommendation that needed the same-work check.
    calls: list[LLMCall] = []
    validation_latency_s: float | None = None
    raw_text: str | None = None  # the reply that failed to parse, or the recommendation reply
    parsed: RecommendationList | None = None
    validation: ValidationResult | None = None
    error: str | None = None  # interpretation failure, LLM failure or unparseable reply

    @property
    def passed(self) -> bool:
        return self.error is None and self.validation is not None and self.validation.passed

    @property
    def total_usage(self) -> Usage | None:
        return total_usage(self.calls)


async def run_baseline(
    profile: Profile,
    flags: Filters,
    *,
    request: str | None = None,
    llm: LLMClient,
    books: BookSearch,
    works: WorkResolver | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float | None = None,
) -> BaselineResult:
    """Interpret the optional request, merge its filters with `flags`, recommend, validate.

    Explicit `flags` override interpreted filters field by field. A failed interpretation
    stops the run before the recommendation call, so typed constraints are never dropped.
    With `works`, equal work keys settle the same-work question before the LLM judge.
    """
    result = BaselineResult(prompt_id=baseline_v1.PROMPT_ID)
    text = request.strip() if request else None

    if text:
        interpretation = await interpret_request(text, llm=llm)
        result = result.model_copy(
            update={
                "request_prompt_id": interpretation.prompt_id,
                "request_text": text,
                "calls": [interpretation.call] if interpretation.call else [],
            }
        )
        if interpretation.parsed is None:
            return result.model_copy(
                update={
                    "raw_text": interpretation.raw_text,
                    "error": f"Could not interpret the request: {interpretation.error}",
                }
            )
        filters, sources = merge_filters(interpretation.parsed.filters, flags)
        preference = interpretation.parsed.preference
    else:
        filters, sources = merge_filters(Filters(), flags)
        preference = None

    resolved = ResolvedRequest(text=text, preference=preference, filters=filters, sources=sources)
    result = result.model_copy(update={"request": resolved})

    started = perf_counter()
    try:
        response = await llm.complete(
            system=baseline_v1.SYSTEM,
            messages=[Message.user(baseline_v1.build_user_prompt(profile, filters, preference))],
            max_tokens=max_tokens,
            temperature=temperature,
        )
    except LLMError as e:
        failed = LLMCall(name=CALL_NAME, model=None, usage=None, latency_s=perf_counter() - started)
        return result.model_copy(
            update={"calls": [*result.calls, failed], "error": f"LLM call failed: {e}"}
        )

    call = LLMCall(
        name=CALL_NAME,
        model=response.model,
        usage=response.usage,
        latency_s=perf_counter() - started,
    )
    reply = response.message.text
    result = result.model_copy(update={"calls": [*result.calls, call], "raw_text": reply})

    try:
        parsed = parse_recommendations(reply)
    except RecommendationParseError as e:
        reason = str(e)
        if response.stop_reason is StopReason.MAX_TOKENS:
            reason += " (the reply was cut off at max_tokens)"
        return result.model_copy(update={"error": f"Unparseable reply: {reason}"})

    started = perf_counter()
    judge = LLMSameWorkJudge(llm)
    validation = await validate_recommendations(
        parsed.recommendations, profile, filters, books, WorkMatching(judge=judge, resolver=works)
    )
    return result.model_copy(
        update={
            "calls": [*result.calls, *judge.calls],
            "parsed": parsed,
            "validation": validation,
            "validation_latency_s": perf_counter() - started,
        }
    )


def parse_recommendations(text: str) -> RecommendationList:
    """Read the model's JSON reply, tolerating code fences and prose around the JSON.

    Accepts {"recommendations": [...]} or a bare [...] list. Raises
    RecommendationParseError with a specific reason instead of crashing.
    """
    try:
        payload = extract_json(text)
        if isinstance(payload, list):
            payload = {"recommendations": cast(list[object], payload)}
        return parse_model(RecommendationList, payload)
    except ReplyParseError as e:
        raise RecommendationParseError(str(e)) from e
