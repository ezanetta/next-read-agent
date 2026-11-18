"""Interpreting a reader's free-text request with a short, separate LLM call.

Kept apart from recommending on purpose: if one reply both interpreted and recommended,
a misread filter would pass validation unnoticed. Shared by the baseline and the agent.
interpret_request never raises: problems end up in Interpretation.error.
"""

from time import perf_counter

from pydantic import BaseModel

from book_agent.domain.recommendations import Filters
from book_agent.domain.requests import (
    MAX_REQUEST_CHARS,
    ParsedRequest,
    ResolvedRequest,
    merge_filters,
)
from book_agent.domain.validation import primary_language
from book_agent.json_reply import ReplyParseError, extract_json, parse_model
from book_agent.llm.base import LLMClient, LLMError, Message
from book_agent.llm.calls import LLMCall
from book_agent.prompts import request_parser_v1, request_parser_v2
from book_agent.prompts.parser_prompt import ParserPrompt

CALL_NAME = "interpret_request"
PARSER_PROMPTS: dict[str, ParserPrompt] = {
    p.prompt_id: p for p in (request_parser_v1.PROMPT, request_parser_v2.PROMPT)
}
DEFAULT_PARSER_PROMPT = request_parser_v2.PROMPT  # F-009: v1 drops soft constraints
DEFAULT_MAX_TOKENS = 512  # room for a little thinking on top of a tiny JSON answer


class Interpretation(BaseModel, frozen=True):
    prompt_id: str
    parsed: ParsedRequest | None = None
    error: str | None = None
    call: LLMCall | None = None  # None when the request was rejected before calling
    raw_text: str | None = None


class _Reply(BaseModel):
    preference: str | None = None
    language: str | None = None
    max_pages: int | None = None


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
        return Interpretation(prompt_id=prompt_id, error="the request is empty")
    if len(request) > MAX_REQUEST_CHARS:
        return Interpretation(
            prompt_id=prompt_id,
            error=f"the request is {len(request)} characters; the limit is {MAX_REQUEST_CHARS}",
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
        return Interpretation(prompt_id=prompt_id, error=f"LLM call failed: {e}", call=failed)

    call = LLMCall(
        name=CALL_NAME,
        model=response.model,
        usage=response.usage,
        latency_s=perf_counter() - started,
    )
    reply = response.message.text
    try:
        parsed = parse_interpretation(reply)
    except ReplyParseError as e:
        return Interpretation(prompt_id=prompt_id, error=str(e), call=call, raw_text=reply)
    return Interpretation(prompt_id=prompt_id, parsed=parsed, call=call, raw_text=reply)


def parse_interpretation(text: str) -> ParsedRequest:
    """Read the interpreter's JSON reply. Raises ReplyParseError with a specific reason.

    Tolerates code fences, a literal newline inside a string, an upper-case or regional
    language code ("EN", "en-US"), and a blank preference; rejects anything that isn't an
    ISO 639-1 code ("english").
    """
    # strict=False: a literal newline inside the preference string is harmless (F-010).
    # Only here: the baseline's parsing, and so its measured behavior, stays strict.
    reply = parse_model(_Reply, extract_json(text, strict=False))
    language = primary_language(reply.language) if reply.language else None
    filters = parse_model(Filters, {"language": language, "max_pages": reply.max_pages})
    preference = reply.preference.strip() if reply.preference else None
    return ParsedRequest(preference=preference or None, filters=filters)


class RequestResolution(BaseModel, frozen=True):
    """The request to act on, and the interpretation behind it (if there was text)."""

    request: ResolvedRequest | None  # None when interpretation failed: stop the run (D-007)
    interpretation: Interpretation | None  # None when there was no request text


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
        return RequestResolution(request=None, interpretation=interpretation)
    filters, sources = merge_filters(interpretation.parsed.filters, flags)
    resolved = ResolvedRequest(
        text=request,
        preference=interpretation.parsed.preference,
        filters=filters,
        sources=sources,
    )
    return RequestResolution(request=resolved, interpretation=interpretation)
