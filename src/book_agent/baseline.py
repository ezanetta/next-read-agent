"""The no-tools baseline: one LLM call with the whole profile, then hard-rule validation.

The simplest end-to-end run of the project and the first baseline for evals.
run_baseline never raises for model or parsing problems: they end up in result.error, so
an eval run always gets one result per case.
"""

import json
import re
from time import perf_counter
from typing import cast

from pydantic import BaseModel, ValidationError

from book_agent.domain.models import Profile
from book_agent.domain.recommendations import Filters, RecommendationList, ValidationResult
from book_agent.domain.validation import validate_recommendations
from book_agent.llm.base import LLMClient, LLMError, Message, StopReason, Usage
from book_agent.ports import BookSearch
from book_agent.prompts import baseline_v1

DEFAULT_MAX_TOKENS = 2048

_FENCED = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


class RecommendationParseError(ValueError):
    """The model's reply could not be read as a recommendation list."""


class BaselineResult(BaseModel, frozen=True):
    prompt_id: str
    model: str | None = None
    usage: Usage | None = None
    llm_latency_s: float
    validation_latency_s: float | None = None
    raw_text: str | None = None
    parsed: RecommendationList | None = None
    validation: ValidationResult | None = None
    error: str | None = None  # LLM failure or unparseable reply

    @property
    def passed(self) -> bool:
        return self.error is None and self.validation is not None and self.validation.passed


async def run_baseline(
    profile: Profile,
    filters: Filters,
    *,
    llm: LLMClient,
    books: BookSearch,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float | None = None,
) -> BaselineResult:
    prompt_id = baseline_v1.PROMPT_ID
    started = perf_counter()
    try:
        response = await llm.complete(
            system=baseline_v1.SYSTEM,
            messages=[Message.user(baseline_v1.build_user_prompt(profile, filters))],
            max_tokens=max_tokens,
            temperature=temperature,
        )
    except LLMError as e:
        return BaselineResult(
            prompt_id=prompt_id,
            llm_latency_s=perf_counter() - started,
            error=f"LLM call failed: {e}",
        )
    llm_latency = perf_counter() - started
    text = response.message.text
    common = {
        "prompt_id": prompt_id,
        "model": response.model,
        "usage": response.usage,
        "llm_latency_s": llm_latency,
        "raw_text": text,
    }

    try:
        parsed = parse_recommendations(text)
    except RecommendationParseError as e:
        reason = str(e)
        if response.stop_reason is StopReason.MAX_TOKENS:
            reason += " (the reply was cut off at max_tokens)"
        return BaselineResult.model_validate(common | {"error": f"Unparseable reply: {reason}"})

    started = perf_counter()
    validation = await validate_recommendations(parsed.recommendations, profile, filters, books)
    return BaselineResult.model_validate(
        common
        | {
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
    candidates: list[str] = []
    fenced = _FENCED.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(text.strip())
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start : end + 1])

    error = "no JSON found in the reply"
    for candidate in candidates:
        if not candidate.startswith(("{", "[")):
            continue  # prose, not JSON: keep "no JSON found" unless a real candidate fails
        try:
            payload = cast(object, json.loads(candidate))
        except json.JSONDecodeError as e:
            error = f"invalid JSON ({e.msg} at line {e.lineno}, column {e.colno})"
            continue
        if isinstance(payload, list):
            payload = {"recommendations": cast(list[object], payload)}
        try:
            return RecommendationList.model_validate(payload)
        except ValidationError as e:
            raise RecommendationParseError(_describe(e)) from e
    raise RecommendationParseError(error)


def _describe(error: ValidationError) -> str:
    first = error.errors()[0]
    location = ".".join(str(part) for part in first["loc"]) or "reply"
    more = f" (and {error.error_count() - 1} more)" if error.error_count() > 1 else ""
    return f"unexpected JSON shape at {location}: {first['msg']}{more}"
