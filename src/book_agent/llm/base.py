"""Provider-agnostic LLM interface.

Defines the LLMClient Protocol and the shared types every adapter speaks: Message (with
TextPart, ToolCallPart and ToolResultPart), ToolSpec, StopReason, Usage and LLMResponse,
plus the LLM error hierarchy and small helpers adapters share.

Imports only Pydantic and the standard library: no provider SDKs.
"""

import secrets
from collections.abc import Sequence
from enum import StrEnum
from typing import Annotated, Literal, Protocol, Self

from pydantic import BaseModel, Field, JsonValue, model_validator

Role = Literal["user", "assistant"]
JsonObject = dict[str, JsonValue]

# Valid for both Claude (^[a-zA-Z0-9_-]{1,64}$) and Gemini (must start with a letter or _).
TOOL_NAME_PATTERN = r"^[a-zA-Z_][a-zA-Z0-9_-]{0,63}$"


# --- Errors -----------------------------------------------------------------


class LLMError(Exception):
    """Any failure talking to an LLM provider. The agent loop catches this."""


class LLMRateLimitError(LLMError):
    """The provider rejected the request for rate or quota reasons."""

    def __init__(
        self, message: str, *, retry_after: float | None = None, daily: bool = False
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after  # seconds, when the provider suggests one
        self.daily = daily  # a daily quota: waiting minutes won't help


class LLMTransientError(LLMError):
    """A failure that may not happen again: a server error or a network error.
    A retrying client resends these (D-030); adapters never do."""

    def __init__(self, message: str, *, retries: int = 0) -> None:
        super().__init__(message)
        self.retries = retries  # times a retrying client resent the request before giving up

    def after_retry(self) -> "LLMTransientError":
        """The same error, marked as having survived one more retry."""
        return LLMTransientError(f"{self} (after 1 retry)", retries=self.retries + 1)


class LLMServerError(LLMTransientError):
    """The provider failed on its side (HTTP 5xx), e.g. 503 high demand."""

    def __init__(self, message: str, *, status: int, retries: int = 0) -> None:
        super().__init__(message, retries=retries)
        self.status = status  # the HTTP status code

    def after_retry(self) -> "LLMServerError":
        return LLMServerError(
            f"{self} (after 1 retry)", status=self.status, retries=self.retries + 1
        )


class LLMNetworkError(LLMTransientError):
    """The request never got an answer: a timeout or a connection failure."""

    def after_retry(self) -> "LLMNetworkError":
        return LLMNetworkError(f"{self} (after 1 retry)", retries=self.retries + 1)


class LLMRefusalError(LLMError):
    """The provider refused or blocked the response (Claude refusal, Gemini SAFETY, ...)."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason  # the provider's own value, e.g. "refusal" or "SAFETY"


# --- Message parts ----------------------------------------------------------


ProviderMetadata = dict[str, str]
"""Opaque provider data that must travel back with a part, e.g. Gemini thought signatures.

Keys are namespaced by provider ("gemini.thought_signature"); values are strings
(base64 for binary data) so conversations stay JSON-serializable. Only adapters read
it; the agent loop keeps it by appending response messages to the history unchanged.
"""


class TextPart(BaseModel, frozen=True):
    type: Literal["text"] = "text"
    text: str
    metadata: ProviderMetadata | None = None


class ToolCallPart(BaseModel, frozen=True):
    """The model asks to run a tool. `id` pairs it with its ToolResultPart."""

    type: Literal["tool_call"] = "tool_call"
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments: JsonObject
    metadata: ProviderMetadata | None = None


class ToolResultPart(BaseModel, frozen=True):
    """The output of a tool call, sent back to the model in a user message."""

    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str = Field(min_length=1)
    content: str
    is_error: bool = False


Part = Annotated[TextPart | ToolCallPart | ToolResultPart, Field(discriminator="type")]
"""A tagged union: the `type` field tells Pydantic which class to build from JSON."""


# --- Messages ---------------------------------------------------------------


class Message(BaseModel, frozen=True):
    """One conversation turn. The system prompt is not a message: see LLMClient.complete."""

    role: Role
    parts: tuple[Part, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _parts_match_role(self) -> Self:
        if self.role == "user" and any(isinstance(p, ToolCallPart) for p in self.parts):
            raise ValueError("user messages cannot contain tool calls")
        if self.role == "assistant" and any(isinstance(p, ToolResultPart) for p in self.parts):
            raise ValueError("assistant messages cannot contain tool results")
        return self

    @classmethod
    def user(cls, text: str) -> Self:
        return cls(role="user", parts=(TextPart(text=text),))

    @classmethod
    def tool_results(cls, *results: ToolResultPart) -> Self:
        if not results:
            raise ValueError("tool_results needs at least one result")
        return cls(role="user", parts=results)

    @property
    def text(self) -> str:
        """Non-empty text parts joined with newlines; empty if there are none.

        Empty text parts can exist only to carry provider metadata, so they are skipped.
        """
        return "\n".join(p.text for p in self.parts if isinstance(p, TextPart) and p.text)

    @property
    def tool_calls(self) -> list[ToolCallPart]:
        return [p for p in self.parts if isinstance(p, ToolCallPart)]


# --- Tools ------------------------------------------------------------------


class ToolSpec(BaseModel, frozen=True):
    """A tool the model may call. `parameters` is a JSON Schema for the arguments."""

    name: str = Field(pattern=TOOL_NAME_PATTERN)
    description: str = Field(min_length=1)
    parameters: JsonObject

    @classmethod
    def from_model(cls, name: str, description: str, args: type[BaseModel]) -> Self:
        """Build a spec whose schema is generated from a Pydantic model.

        The schema is kept as Pydantic generates it; adapters adapt it if a provider needs.
        Validate the model's arguments back with `args.model_validate(call.arguments)`.
        """
        return cls(name=name, description=description, parameters=args.model_json_schema())


# --- Responses --------------------------------------------------------------


class ToolChoice(BaseModel, frozen=True):
    """Makes a request call one of the named tools: no text-only answer, no other tool.

    Passing None instead lets the model choose (text, or any declared tool). Every name
    must be one of the request's tools; adapters raise ValueError otherwise.

    Provider mapping:
    - Gemini: function calling mode ANY with allowed_function_names = names.
    - Claude: one name -> tool_choice {"type": "tool", "name": name}; every declared tool
      -> {"type": "any"}; a subset of several -> send only those tools, with
      {"type": "any"} (Claude has no "one of these" option).
    """

    names: tuple[str, ...] = Field(min_length=1)

    @classmethod
    def required(cls, *names: str) -> Self:
        return cls(names=names)

    def check_against(self, tools: Sequence["ToolSpec"]) -> None:
        """Raise ValueError if a name isn't among the request's tools."""
        declared = {t.name for t in tools}
        unknown = [n for n in self.names if n not in declared]
        if unknown:
            raise ValueError(f"tool_choice names undeclared tools: {', '.join(unknown)}")


class StopReason(StrEnum):
    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"


class Usage(BaseModel, frozen=True):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)  # includes thinking tokens when the provider bills them


class LLMResponse(BaseModel, frozen=True):
    message: Message
    stop_reason: StopReason
    usage: Usage
    model: str
    retries: int = Field(default=0, ge=0)  # set by a retrying client; adapters leave it at 0

    @model_validator(mode="after")
    def _message_is_from_assistant(self) -> Self:
        if self.message.role != "assistant":
            raise ValueError("an LLMResponse message must have role 'assistant'")
        return self


# --- The client contract ----------------------------------------------------


class LLMClient(Protocol):
    """One LLM provider. Adapters live next to this module (gemini.py, claude.py)."""

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
        """Send one request and return the assistant's next message.

        `temperature=None` uses the provider's default. `tool_choice` forces a call to
        one of the named tools; None lets the model choose.
        Raises LLMRateLimitError, LLMServerError, LLMNetworkError, LLMRefusalError or
        LLMError on failure.
        """
        ...


# --- Helpers for adapters ---------------------------------------------------


def new_tool_call_id() -> str:
    """A unique id for providers that omit tool call ids (some Gemini versions)."""
    return f"call_{secrets.token_hex(12)}"


def tool_names_by_call_id(messages: Sequence[Message]) -> dict[str, str]:
    """Map each tool call id to its tool name, walking the history in order.

    Gemini needs the function name when sending a result back; ToolResultPart only
    carries the call id. Raises LLMError if a result has no earlier matching call.
    """
    names: dict[str, str] = {}
    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                names[part.id] = part.name
            elif isinstance(part, ToolResultPart) and part.tool_call_id not in names:
                raise LLMError(
                    f"tool result {part.tool_call_id!r} has no matching earlier tool call"
                )
    return names
