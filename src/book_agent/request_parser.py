"""Interpreting a reader's free-text request with a short, separate LLM call.

Kept apart from recommending on purpose: if one reply both interpreted and recommended,
a misread filter would pass validation unnoticed. Shared by the baseline and the agent.
interpret_request never raises: problems end up in Interpretation.error.
"""

from time import perf_counter
from typing import Literal

from pydantic import BaseModel

from book_agent.domain.recommendations import Filters
from book_agent.domain.requests import (
    MAX_REQUEST_CHARS,
    ConflictingFilters,
    ExtractedRequest,
    ParsedRequest,
    ResolvedRequest,
    merge_filters,
)
from book_agent.domain.validation import primary_language
from book_agent.json_reply import ReplyParseError, extract_json, parse_model
from book_agent.llm.base import LLMClient, LLMError, Message
from book_agent.llm.calls import LLMCall
from book_agent.prompts import request_parser_v1, request_parser_v2, request_parser_v3
from book_agent.prompts.parser_prompt import ParserPrompt

CALL_NAME = "interpret_request"
PARSER_PROMPTS: dict[str, ParserPrompt] = {
    p.prompt_id: p
    for p in (request_parser_v1.PROMPT, request_parser_v2.PROMPT, request_parser_v3.PROMPT)
}
DEFAULT_PARSER_PROMPT = request_parser_v3.PROMPT  # D-026: min_pages (v1 also had F-009)
DEFAULT_MAX_TOKENS = 512  # room for a little thinking on top of a tiny JSON answer


InterpretationError = Literal["rejected", "llm_error", "unparseable", "contradiction"]
"""rejected: empty or too long, before any call. contradiction: the text's own bounds
conflict (a minimum above the maximum), detected in code (D-026)."""


class Interpretation(BaseModel, frozen=True):
    prompt_id: str
    parsed: ParsedRequest | None = None
    extracted: ExtractedRequest | None = None  # what the model said, also on contradiction
    error: str | None = None
    error_kind: InterpretationError | None = None
    call: LLMCall | None = None  # None when the request was rejected before calling
    raw_text: str | None = None


class _Reply(BaseModel):
    preference: str | None = None
    language: str | None = None
    max_pages: int | None = None
    min_pages: int | None = None  # asked for from request_parser_v3 on (D-026)


async def interpret_request(
    text: str,
    *,
    llm: LLMClient,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    prompt: ParserPrompt = DEFAULT_PARSER_PROMPT,
) -> Interpretation:
    prompt_id = prompt.prompt_id
    request = text.strip()
    if not request:
        return Interpretation(
            prompt_id=prompt_id, error="the request is empty", error_kind="rejected"
        )
    if len(request) > MAX_REQUEST_CHARS:
        return Interpretation(
            prompt_id=prompt_id,
            error=f"the request is {len(request)} characters; the limit is {MAX_REQUEST_CHARS}",
            error_kind="rejected",
        )

    started = perf_counter()
    try:
        response = await llm.complete(
            system=prompt.system,
            messages=[Message.user(prompt.build_user_prompt(request))],
            max_tokens=max_tokens,
            temperature=0.0,  # extraction should give the same answer every time
        )
    except LLMError as e:
        failed = LLMCall(name=CALL_NAME, model=None, usage=None, latency_s=perf_counter() - started)
        return Interpretation(
            prompt_id=prompt_id, error=f"LLM call failed: {e}", error_kind="llm_error", call=failed
        )

    call = LLMCall(
        name=CALL_NAME,
        model=response.model,
        usage=response.usage,
        latency_s=perf_counter() - started,
    )
    reply = response.message.text
    try:
        extracted = extract_interpretation(reply)
    except ReplyParseError as e:
        return Interpretation(
            prompt_id=prompt_id, error=str(e), error_kind="unparseable", call=call, raw_text=reply
        )
    try:
        parsed = extracted.to_parsed()
    except ConflictingFilters as e:
        return Interpretation(
            prompt_id=prompt_id,
            extracted=extracted,
            error=str(e),
            error_kind="contradiction",
            call=call,
            raw_text=reply,
        )
    return Interpretation(
        prompt_id=prompt_id, parsed=parsed, extracted=extracted, call=call, raw_text=reply
    )


def extract_interpretation(text: str) -> ExtractedRequest:
    """Read the interpreter's JSON reply literally. Raises ReplyParseError with a reason.

    Tolerates code fences, a literal newline inside a string, an upper-case or regional
    language code ("EN", "en-US"), and a blank preference; rejects anything that isn't an
    ISO 639-1 code ("english"). Does not compare the bounds: see ExtractedRequest.
    """
    # strict=False: a literal newline inside the preference string is harmless (F-010).
    # Only here: the baseline's parsing, and so its measured behavior, stays strict.
    reply = parse_model(_Reply, extract_json(text, strict=False))
    preference = reply.preference.strip() if reply.preference else None
    return parse_model(
        ExtractedRequest,
        {
            "preference": preference or None,
            "language": primary_language(reply.language) if reply.language else None,
            "max_pages": reply.max_pages,
            "min_pages": reply.min_pages,
        },
    )


def parse_interpretation(text: str) -> ParsedRequest:
    """extract_interpretation, then the filters. Raises ReplyParseError, or
    ConflictingFilters when the text's own bounds conflict."""
    return extract_interpretation(text).to_parsed()


class RequestResolution(BaseModel, frozen=True):
    """The request to act on, and the interpretation behind it (if there was text)."""

    request: ResolvedRequest | None  # None when it can't be acted on: stop the run (D-007)
    interpretation: Interpretation | None  # None when there was no request text
    error: str | None = None  # why request is None
    # "contradiction": conflicting bounds, in the text or between text and flags (D-026).
    error_kind: Literal["interpretation", "contradiction"] | None = None


async def resolve_request(text: str | None, flags: Filters, *, llm: LLMClient) -> RequestResolution:
    """Interpret the optional request text and merge its filters with `flags`.

    Explicit flags override interpreted filters field by field (D-008). Blank text counts
    as no request: no LLM call. Shared by the baseline and the agent.
    """
    request = text.strip() if text else None
    if not request:
        filters, sources = merge_filters(Filters(), flags)
        resolved = ResolvedRequest(text=None, preference=None, filters=filters, sources=sources)
        return RequestResolution(request=resolved, interpretation=None)

    interpretation = await interpret_request(request, llm=llm)
    if interpretation.parsed is None:
        if interpretation.error_kind == "contradiction":
            return RequestResolution(
                request=None,
                interpretation=interpretation,
                error=f"The request contradicts itself: {interpretation.error}",
                error_kind="contradiction",
            )
        return RequestResolution(
            request=None,
            interpretation=interpretation,
            error=f"Could not interpret the request: {interpretation.error}",
            error_kind="interpretation",
        )
    try:
        filters, sources = merge_filters(interpretation.parsed.filters, flags)
    except ConflictingFilters as e:  # a minimum in the text above a maximum given as a flag
        return RequestResolution(
            request=None,
            interpretation=interpretation,
            error=f"The request contradicts the flags: {e}",
            error_kind="contradiction",
        )
    resolved = ResolvedRequest(
        text=request,
        preference=interpretation.parsed.preference,
        filters=filters,
        sources=sources,
    )
    return RequestResolution(request=resolved, interpretation=interpretation)
