"""Gemini adapter: implements LLMClient on top of the google-genai SDK.

The translation lives in pure functions (to_gemini_contents, to_gemini_config,
from_gemini_response, to_llm_error) so it can be tested with SDK types and no network.
GeminiClient only wires them around the async API call.
"""

import base64
import re
from collections.abc import Sequence
from typing import cast

import httpx2
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, JsonValue, ValidationError

from book_agent.config import GeminiThinkingLevel
from book_agent.llm.base import (
    LLMError,
    LLMRateLimitError,
    LLMRefusalError,
    LLMResponse,
    Message,
    ProviderMetadata,
    StopReason,
    TextPart,
    ToolCallPart,
    ToolChoice,
    ToolResultPart,
    ToolSpec,
    Usage,
    new_tool_call_id,
    tool_names_by_call_id,
)

SIGNATURE_KEY = "gemini.thought_signature"  # base64 of Part.thought_signature
GENERATED_ID_KEY = "gemini.generated_id"  # "1" when Gemini omitted the call id

FR = types.FinishReason
REFUSAL_REASONS = frozenset(
    {
        FR.SAFETY,
        FR.RECITATION,
        FR.BLOCKLIST,
        FR.PROHIBITED_CONTENT,
        FR.SPII,
        FR.MODEL_ARMOR,
        FR.IMAGE_SAFETY,
        FR.IMAGE_PROHIBITED_CONTENT,
        FR.IMAGE_RECITATION,
    }
)

_THINKING_LEVELS: dict[GeminiThinkingLevel, types.ThinkingLevel] = {
    "minimal": types.ThinkingLevel.MINIMAL,
    "low": types.ThinkingLevel.LOW,
    "medium": types.ThinkingLevel.MEDIUM,
    "high": types.ThinkingLevel.HIGH,
}

_RETRY_DELAY = re.compile(r"^(\d+(?:\.\d+)?)s$")  # protobuf Duration, e.g. "30s", "1.5s"


class GeminiClient:
    """LLMClient backed by Gemini. The genai.Client is injected and owns the API key."""

    def __init__(
        self,
        client: genai.Client,
        *,
        model: str,
        thinking_level: GeminiThinkingLevel | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._thinking_level: GeminiThinkingLevel | None = thinking_level

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        max_tokens: int,
        temperature: float | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> LLMResponse:
        contents = to_gemini_contents(messages)
        config = to_gemini_config(
            system=system,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            thinking_level=self._thinking_level,
            tool_choice=tool_choice,
        )
        try:
            # The SDK's `contents` union mentions types from optional deps (e.g. PIL).
            response = await self._client.aio.models.generate_content(  # pyright: ignore[reportUnknownMemberType]
                model=self._model, contents=contents, config=config
            )
        except errors.APIError as e:
            raise to_llm_error(e) from e
        # google-genai 3.x talks HTTP through httpx2 (not httpx) and doesn't wrap its errors.
        except httpx2.HTTPError as e:
            raise LLMError(f"Gemini request failed: {type(e).__name__}") from e
        return from_gemini_response(response, requested_model=self._model)


# --- Request: our types -> SDK types ----------------------------------------


def to_gemini_contents(messages: Sequence[Message]) -> list[types.Content]:
    """Translate the history part by part, in order, never merging parts.

    Raises LLMError if a tool result has no earlier matching tool call.
    """
    names = tool_names_by_call_id(messages)
    generated = {
        p.id
        for m in messages
        for p in m.parts
        if isinstance(p, ToolCallPart) and p.metadata and GENERATED_ID_KEY in p.metadata
    }
    return [
        types.Content(
            role="model" if m.role == "assistant" else "user",
            parts=[_to_gemini_part(p, names, generated) for p in m.parts],
        )
        for m in messages
    ]


def _to_gemini_part(
    part: TextPart | ToolCallPart | ToolResultPart, names: dict[str, str], generated: set[str]
) -> types.Part:
    match part:
        case TextPart():
            return types.Part(text=part.text, thought_signature=_decode_signature(part.metadata))
        case ToolCallPart():
            return types.Part(
                function_call=types.FunctionCall(
                    id=None if part.id in generated else part.id,
                    name=part.name,
                    args=dict(part.arguments),
                ),
                thought_signature=_decode_signature(part.metadata),
            )
        case ToolResultPart():
            key = "error" if part.is_error else "result"
            return types.Part(
                function_response=types.FunctionResponse(
                    id=None if part.tool_call_id in generated else part.tool_call_id,
                    name=names[part.tool_call_id],
                    response={key: part.content},
                )
            )


def to_gemini_config(
    *,
    system: str,
    tools: Sequence[ToolSpec],
    max_tokens: int,
    temperature: float | None,
    thinking_level: GeminiThinkingLevel | None,
    tool_choice: ToolChoice | None = None,
) -> types.GenerateContentConfig:
    """Raises ValueError if tool_choice names a tool that isn't declared."""
    if tool_choice is not None:
        tool_choice.check_against(tools)
    declarations = [
        types.FunctionDeclaration(
            name=t.name, description=t.description, parameters_json_schema=t.parameters
        )
        for t in tools
    ]
    return types.GenerateContentConfig(
        system_instruction=system or None,
        tools=[types.Tool(function_declarations=declarations)] if declarations else None,
        # ANY with allowed names: the model must call one of them (ToolChoice).
        tool_config=(
            types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.ANY,
                    allowed_function_names=list(tool_choice.names),
                )
            )
            if tool_choice is not None
            else None
        ),
        max_output_tokens=max_tokens,
        temperature=temperature,
        thinking_config=(
            types.ThinkingConfig(thinking_level=_THINKING_LEVELS[thinking_level])
            if thinking_level
            else None
        ),
        # No retries inside the adapter, whatever the injected client was built with.
        http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)),
    )


# --- Response: SDK types -> our types ---------------------------------------


def from_gemini_response(
    response: types.GenerateContentResponse, *, requested_model: str
) -> LLMResponse:
    """Translate one response, raising LLMRefusalError or LLMError when it is unusable."""
    feedback = response.prompt_feedback
    if feedback and feedback.block_reason:
        reason = feedback.block_reason.value
        raise LLMRefusalError(f"Gemini blocked the prompt: {reason}", reason=reason)
    if not response.candidates:
        raise LLMError("Gemini returned no candidates")

    candidate = response.candidates[0]
    finish = candidate.finish_reason
    if finish is not None and finish in REFUSAL_REASONS:
        raise LLMRefusalError(f"Gemini refused to answer: {finish.value}", reason=finish.value)

    raw_parts = candidate.content.parts if candidate.content and candidate.content.parts else []
    try:
        parts = [p for raw in raw_parts if (p := _from_gemini_part(raw))]
    except ValidationError as e:
        raise LLMError(f"Gemini returned a malformed part: {e.error_count()} errors") from e

    calls = [p for p in parts if isinstance(p, ToolCallPart)]
    has_content = bool(calls) or any(isinstance(p, TextPart) and p.text for p in parts)

    if finish == FR.MAX_TOKENS:
        if not has_content:
            raise LLMError(
                "Gemini used up max_tokens without producing any output, likely on thinking: "
                "raise max_tokens or lower GEMINI_THINKING_LEVEL"
            )
        stop_reason = StopReason.MAX_TOKENS
    elif finish is None or finish == FR.STOP:
        if not has_content:
            raise LLMError("Gemini returned an empty response")
        stop_reason = StopReason.TOOL_USE if calls else StopReason.END_TURN
    else:
        raise LLMError(f"Gemini stopped without a usable answer: {finish.value}")

    return LLMResponse(
        message=Message(role="assistant", parts=tuple(parts)),
        stop_reason=stop_reason,
        usage=_usage(response.usage_metadata),
        model=response.model_version or requested_model,
    )


def _from_gemini_part(part: types.Part) -> TextPart | ToolCallPart | None:
    if part.thought:  # thought summaries; we never request them, so skip defensively
        return None
    metadata: ProviderMetadata = {}
    if part.thought_signature:
        metadata[SIGNATURE_KEY] = base64.b64encode(part.thought_signature).decode("ascii")

    if part.function_call:
        call = part.function_call
        call_id = call.id
        if not call_id:
            call_id = new_tool_call_id()
            metadata[GENERATED_ID_KEY] = "1"
        return ToolCallPart(
            id=call_id,
            name=call.name or "",
            arguments=call.args or {},
            metadata=metadata or None,
        )
    if part.text or metadata:  # keep empty text parts that only carry a signature
        return TextPart(text=part.text or "", metadata=metadata or None)
    return None


def _usage(metadata: types.GenerateContentResponseUsageMetadata | None) -> Usage:
    if metadata is None:
        return Usage(input_tokens=0, output_tokens=0)
    output = (metadata.candidates_token_count or 0) + (metadata.thoughts_token_count or 0)
    return Usage(input_tokens=metadata.prompt_token_count or 0, output_tokens=output)


def _decode_signature(metadata: ProviderMetadata | None) -> bytes | None:
    if metadata and SIGNATURE_KEY in metadata:
        return base64.b64decode(metadata[SIGNATURE_KEY])
    return None


# --- Errors -----------------------------------------------------------------


class _ErrorDetail(BaseModel):
    details: list[dict[str, JsonValue]] = []


class _ErrorBody(BaseModel):
    error: _ErrorDetail


def to_llm_error(error: errors.APIError) -> LLMError:
    """Map an SDK error. Only HTTP 429 is a rate limit: 402 also says RESOURCE_EXHAUSTED
    but means prepaid credit ran out, which waiting won't fix."""
    message = f"Gemini API error {error.code} {error.status}: {error.message}"
    if error.code == 429:
        # APIError.details is untyped in the SDK: it holds the raw error JSON.
        details = cast(object, error.details)  # pyright: ignore[reportUnknownMemberType]
        return LLMRateLimitError(
            message, retry_after=_retry_delay(details), daily=_is_daily_quota(details)
        )
    return LLMError(message)


def _is_daily_quota(details: object) -> bool:
    """Whether a google.rpc.QuotaFailure names a per-day quota, e.g. quotaId
    "GenerateRequestsPerDayPerProjectPerModel-FreeTier"."""
    try:
        body = _ErrorBody.model_validate(details)
    except ValidationError:
        return False
    for detail in body.error.details:
        violations = detail.get("violations")
        if str(detail.get("@type", "")).endswith("QuotaFailure") and isinstance(violations, list):
            for violation in violations:
                if isinstance(violation, dict) and "PerDay" in str(violation.get("quotaId", "")):
                    return True
    return False


def _retry_delay(details: object) -> float | None:
    """Read google.rpc.RetryInfo.retryDelay ("30s") from the error JSON, if present."""
    try:
        body = _ErrorBody.model_validate(details)
    except ValidationError:
        return None
    for detail in body.error.details:
        delay = detail.get("retryDelay")
        if str(detail.get("@type", "")).endswith("RetryInfo") and isinstance(delay, str):
            match = _RETRY_DELAY.match(delay)
            if match:
                return float(match.group(1))
    return None
