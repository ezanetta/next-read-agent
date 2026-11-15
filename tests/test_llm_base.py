import re
from collections.abc import Callable

import pytest
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from book_agent.llm.base import (
    LLMError,
    LLMRateLimitError,
    LLMRefusalError,
    LLMResponse,
    Message,
    Role,
    StopReason,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
    Usage,
    new_tool_call_id,
    tool_names_by_call_id,
)


def call(call_id: str = "call_1", name: str = "search_books") -> ToolCallPart:
    return ToolCallPart(id=call_id, name=name, arguments={"query": "dune"})


def result(call_id: str = "call_1", content: str = "[]") -> ToolResultPart:
    return ToolResultPart(tool_call_id=call_id, content=content)


def assistant(*parts: TextPart | ToolCallPart) -> Message:
    return Message(role="assistant", parts=parts)


# --- Message -----------------------------------------------------------------


def test_user_builder_wraps_text() -> None:
    message = Message.user("Recommend me something")
    assert message.role == "user"
    assert message.parts == (TextPart(text="Recommend me something"),)


def test_tool_results_builder_makes_a_user_message() -> None:
    message = Message.tool_results(result("a"), result("b"))
    assert message.role == "user"
    assert message.parts == (result("a"), result("b"))


def test_tool_results_builder_needs_at_least_one_result() -> None:
    with pytest.raises(ValueError, match="at least one"):
        Message.tool_results()


def test_assistant_can_mix_text_and_parallel_tool_calls() -> None:
    message = assistant(TextPart(text="Let me search."), call("a"), call("b", "get_book_details"))
    assert message.text == "Let me search."
    assert [c.id for c in message.tool_calls] == ["a", "b"]


def test_text_joins_text_parts_and_is_empty_without_them() -> None:
    assert assistant(TextPart(text="one"), call(), TextPart(text="two")).text == "one\ntwo"
    assert assistant(call()).text == ""


def test_user_message_cannot_contain_tool_calls() -> None:
    with pytest.raises(ValidationError, match="user messages cannot contain tool calls"):
        Message(role="user", parts=(call(),))


def test_assistant_message_cannot_contain_tool_results() -> None:
    with pytest.raises(ValidationError, match="assistant messages cannot contain tool results"):
        Message(role="assistant", parts=(result(),))


@pytest.mark.parametrize("role", ["user", "assistant"])
def test_message_parts_cannot_be_empty(role: Role) -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        Message(role=role, parts=())


@pytest.mark.parametrize(
    "bad_part",
    [
        lambda: ToolCallPart(id="", name="search_books", arguments={}),
        lambda: ToolCallPart(id="call_1", name="", arguments={}),
        lambda: ToolResultPart(tool_call_id="", content="x"),
    ],
)
def test_ids_and_names_cannot_be_empty(bad_part: Callable[[], object]) -> None:
    with pytest.raises(ValidationError):
        bad_part()


def test_conversation_round_trips_through_json() -> None:
    conversation = [
        Message.user("Something like Dune"),
        assistant(TextPart(text="Searching."), call("a"), call("b", "get_book_details")),
        Message.tool_results(
            result("a"), ToolResultPart(tool_call_id="b", content="boom", is_error=True)
        ),
        assistant(TextPart(text="Here are 3 books.")),
    ]
    adapter = TypeAdapter(list[Message])
    restored = adapter.validate_json(adapter.dump_json(conversation))
    assert restored == conversation
    assert isinstance(restored[1].parts[1], ToolCallPart)  # the tag restored the right class


def test_json_uses_type_tags() -> None:
    dumped = assistant(TextPart(text="hi"), call()).model_dump()
    assert [p["type"] for p in dumped["parts"]] == ["text", "tool_call"]


# --- ToolSpec ----------------------------------------------------------------


class Filters(BaseModel):
    language: str | None = None
    max_pages: int | None = Field(default=None, gt=0)


class SearchArgs(BaseModel):
    """Not used as the description: descriptions are passed explicitly."""

    query: str = Field(description="Free-text search, e.g. a title or author")
    filters: Filters = Filters()


def test_from_model_builds_the_json_schema() -> None:
    spec = ToolSpec.from_model("search_books", "Search the catalog.", SearchArgs)
    assert spec.name == "search_books"
    assert spec.description == "Search the catalog."
    assert spec.parameters == SearchArgs.model_json_schema()
    assert spec.parameters["type"] == "object"
    assert spec.parameters["required"] == ["query"]


def test_from_model_keeps_nested_models_as_defs() -> None:
    spec = ToolSpec.from_model("search_books", "Search the catalog.", SearchArgs)
    defs = spec.parameters["$defs"]
    assert isinstance(defs, dict)
    assert "Filters" in defs


def test_schema_arguments_validate_back_into_the_model() -> None:
    arguments = {"query": "dune", "filters": {"language": "es", "max_pages": 400}}
    args = SearchArgs.model_validate(arguments)
    assert args.filters.max_pages == 400


@pytest.mark.parametrize("name", ["search_books", "_private", "get-details", "a" * 64])
def test_valid_tool_names(name: str) -> None:
    ToolSpec(name=name, description="d", parameters={})


@pytest.mark.parametrize("name", ["", "1starts_with_digit", "has space", "dot.name", "a" * 65])
def test_invalid_tool_names(name: str) -> None:
    with pytest.raises(ValidationError):
        ToolSpec(name=name, description="d", parameters={})


def test_tool_spec_needs_a_description() -> None:
    with pytest.raises(ValidationError):
        ToolSpec(name="search_books", description="", parameters={})


# --- LLMResponse -------------------------------------------------------------


def test_response_message_must_be_from_assistant() -> None:
    with pytest.raises(ValidationError, match="role 'assistant'"):
        LLMResponse(
            message=Message.user("hi"),
            stop_reason=StopReason.END_TURN,
            usage=Usage(input_tokens=1, output_tokens=1),
            model="m",
        )


def test_usage_cannot_be_negative() -> None:
    with pytest.raises(ValidationError):
        Usage(input_tokens=-1, output_tokens=0)


def test_stop_reasons_are_the_normalized_strings() -> None:
    assert [s.value for s in StopReason] == ["end_turn", "tool_use", "max_tokens"]


# --- Errors ------------------------------------------------------------------


def test_specific_errors_are_llm_errors() -> None:
    rate_limited = LLMRateLimitError("slow down", retry_after=12.5)
    refused = LLMRefusalError("blocked", reason="SAFETY")
    assert isinstance(rate_limited, LLMError)
    assert isinstance(refused, LLMError)
    assert rate_limited.retry_after == 12.5
    assert refused.reason == "SAFETY"
    assert str(refused) == "blocked"


def test_retry_after_is_optional() -> None:
    assert LLMRateLimitError("slow down").retry_after is None


# --- Helpers -----------------------------------------------------------------


def test_new_tool_call_id_format_and_uniqueness() -> None:
    ids = {new_tool_call_id() for _ in range(1000)}
    assert len(ids) == 1000
    assert all(re.fullmatch(r"call_[0-9a-f]{24}", i) for i in ids)


def test_tool_names_by_call_id_maps_every_call() -> None:
    history = [
        Message.user("hi"),
        assistant(call("a", "search_books"), call("b", "get_book_details")),
        Message.tool_results(result("a"), result("b")),
        assistant(call("c", "is_in_my_library")),
    ]
    assert tool_names_by_call_id(history) == {
        "a": "search_books",
        "b": "get_book_details",
        "c": "is_in_my_library",
    }


def test_tool_names_by_call_id_with_no_tools() -> None:
    assert tool_names_by_call_id([Message.user("hi")]) == {}


def test_tool_names_by_call_id_rejects_an_orphan_result() -> None:
    history = [
        Message.user("hi"),
        assistant(call("a")),
        Message.tool_results(result("a"), result("ghost")),
    ]
    with pytest.raises(LLMError, match="'ghost' has no matching earlier tool call"):
        tool_names_by_call_id(history)


def test_tool_names_by_call_id_rejects_a_result_before_its_call() -> None:
    history = [Message.tool_results(result("a")), assistant(call("a"))]
    with pytest.raises(LLMError, match="'a' has no matching"):
        tool_names_by_call_id(history)


# --- Provider metadata ---------------------------------------------------------


def test_metadata_defaults_to_none() -> None:
    assert TextPart(text="hi").metadata is None
    assert call().metadata is None


def test_metadata_round_trips_through_json() -> None:
    message = assistant(
        TextPart(text="", metadata={"gemini.thought_signature": "c2lnLWE="}),
        ToolCallPart(
            id="c1",
            name="search_books",
            arguments={},
            metadata={"gemini.thought_signature": "c2lnLWI=", "gemini.generated_id": "1"},
        ),
    )
    assert Message.model_validate_json(message.model_dump_json()) == message


def test_text_skips_empty_parts_that_only_carry_metadata() -> None:
    message = assistant(
        TextPart(text="Answer."), TextPart(text="", metadata={"gemini.thought_signature": "x"})
    )
    assert message.text == "Answer."
