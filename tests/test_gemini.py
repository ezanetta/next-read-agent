import base64
import json
import re
from collections.abc import Callable

import httpx2
import pytest
from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from book_agent.llm.base import (
    LLMError,
    LLMRateLimitError,
    LLMRefusalError,
    Message,
    StopReason,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
)
from book_agent.llm.gemini import (
    GENERATED_ID_KEY,
    SIGNATURE_KEY,
    GeminiClient,
    from_gemini_response,
    to_gemini_config,
    to_gemini_contents,
    to_llm_error,
)

FR = types.FinishReason
MODEL = "gemini-3.5-flash-lite"
SIG_A = b"signature-a"
SIG_B = b"signature-b"


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def sdk_response(
    *parts: types.Part,
    finish: types.FinishReason | None = FR.STOP,
    model_version: str | None = "gemini-3.5-flash-lite-001",
    usage: types.GenerateContentResponseUsageMetadata | None = None,
) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=list(parts)), finish_reason=finish
            )
        ],
        usage_metadata=usage
        or types.GenerateContentResponseUsageMetadata(
            prompt_token_count=10, candidates_token_count=5, thoughts_token_count=20
        ),
        model_version=model_version,
    )


def fc(name: str, call_id: str | None, signature: bytes | None = None, **args: str) -> types.Part:
    return types.Part(
        function_call=types.FunctionCall(id=call_id, name=name, args=args),
        thought_signature=signature,
    )


def translate(response: types.GenerateContentResponse) -> Message:
    return from_gemini_response(response, requested_model=MODEL).message


# --- Request: messages -> contents --------------------------------------------


def test_text_conversation_maps_roles_and_text() -> None:
    history = [
        Message.user("Recommend me a book"),
        Message(role="assistant", parts=(TextPart(text="Sure, any genre?"),)),
        Message.user("Science fiction"),
    ]
    contents = to_gemini_contents(history)
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert [c.parts[0].text for c in contents if c.parts] == [
        "Recommend me a book",
        "Sure, any genre?",
        "Science fiction",
    ]


def test_tool_call_and_results_map_with_names_from_history() -> None:
    history = [
        Message.user("Find Dune"),
        Message(
            role="assistant",
            parts=(
                ToolCallPart(id="c1", name="search_books", arguments={"query": "dune"}),
                ToolCallPart(id="c2", name="get_book_details", arguments={"volume_id": "x"}),
            ),
        ),
        Message.tool_results(
            ToolResultPart(tool_call_id="c1", content='[{"title": "Dune"}]'),
            ToolResultPart(tool_call_id="c2", content="not found", is_error=True),
        ),
    ]
    model_turn, results_turn = to_gemini_contents(history)[1:]

    assert model_turn.parts is not None
    calls = [p.function_call for p in model_turn.parts]
    assert [(c.id, c.name, c.args) for c in calls if c] == [
        ("c1", "search_books", {"query": "dune"}),
        ("c2", "get_book_details", {"volume_id": "x"}),
    ]

    assert results_turn.role == "user"
    assert results_turn.parts is not None
    responses = [p.function_response for p in results_turn.parts]
    assert [(r.id, r.name, r.response) for r in responses if r] == [
        ("c1", "search_books", {"result": '[{"title": "Dune"}]'}),
        ("c2", "get_book_details", {"error": "not found"}),
    ]


def test_signatures_are_sent_back_on_the_exact_part() -> None:
    history = [
        Message.user("Find two books"),
        Message(
            role="assistant",
            parts=(
                TextPart(text="Searching.", metadata={SIGNATURE_KEY: b64(SIG_A)}),
                ToolCallPart(
                    id="c1", name="search", arguments={}, metadata={SIGNATURE_KEY: b64(SIG_B)}
                ),
                ToolCallPart(id="c2", name="search", arguments={}),
            ),
        ),
    ]
    parts = to_gemini_contents(history)[1].parts
    assert parts is not None
    assert [p.thought_signature for p in parts] == [SIG_A, SIG_B, None]


def test_generated_ids_are_omitted_on_the_way_back() -> None:
    history = [
        Message.user("hi"),
        Message(
            role="assistant",
            parts=(
                ToolCallPart(
                    id="call_ours", name="search", arguments={}, metadata={GENERATED_ID_KEY: "1"}
                ),
            ),
        ),
        Message.tool_results(ToolResultPart(tool_call_id="call_ours", content="[]")),
    ]
    contents = to_gemini_contents(history)
    call_part = contents[1].parts[0] if contents[1].parts else None
    result_part = contents[2].parts[0] if contents[2].parts else None
    assert call_part and call_part.function_call and call_part.function_call.id is None
    assert result_part and result_part.function_response
    assert result_part.function_response.id is None
    assert result_part.function_response.name == "search"


def test_orphan_tool_result_raises() -> None:
    history = [
        Message.user("hi"),
        Message.tool_results(ToolResultPart(tool_call_id="x", content="")),
    ]
    with pytest.raises(LLMError, match="no matching earlier tool call"):
        to_gemini_contents(history)


# --- Request: config ------------------------------------------------------------


class SearchArgs(BaseModel):
    query: str


SEARCH = ToolSpec.from_model("search_books", "Search the catalog.", SearchArgs)


def test_config_maps_every_setting() -> None:
    config = to_gemini_config(
        system="You recommend books.",
        tools=[SEARCH],
        max_tokens=2048,
        temperature=0.3,
        thinking_level="low",
    )
    # The SDK types `tools` and `system_instruction` as wide unions (callables, MCP
    # sessions, ...), so check the plain dict of what was set instead.
    dumped = config.model_dump(exclude_none=True, exclude={"http_options"})
    assert dumped == {
        "system_instruction": "You recommend books.",
        "max_output_tokens": 2048,
        "temperature": 0.3,
        "thinking_config": {"thinking_level": types.ThinkingLevel.LOW},
        "tools": [
            {
                "function_declarations": [
                    {
                        "name": "search_books",
                        "description": "Search the catalog.",
                        "parameters_json_schema": SEARCH.parameters,  # passed through unchanged
                    }
                ]
            }
        ],
    }


def test_config_leaves_out_unset_options() -> None:
    config = to_gemini_config(
        system="", tools=[], max_tokens=100, temperature=None, thinking_level=None
    )
    dumped = config.model_dump(exclude_none=True, exclude={"http_options"})
    assert dumped == {"max_output_tokens": 100}


def test_config_disables_sdk_retries() -> None:
    config = to_gemini_config(
        system="s", tools=[], max_tokens=100, temperature=None, thinking_level=None
    )
    assert config.http_options is not None
    assert config.http_options.retry_options is not None
    assert config.http_options.retry_options.attempts == 1


# --- Response: SDK -> LLMResponse -------------------------------------------------


def test_text_reply() -> None:
    result = from_gemini_response(sdk_response(types.Part(text="Hello!")), requested_model=MODEL)
    assert result.stop_reason is StopReason.END_TURN
    assert result.message.role == "assistant"
    assert result.message.text == "Hello!"
    assert result.model == "gemini-3.5-flash-lite-001"


def test_usage_counts_thinking_tokens_as_output() -> None:
    result = from_gemini_response(sdk_response(types.Part(text="hi")), requested_model=MODEL)
    assert (result.usage.input_tokens, result.usage.output_tokens) == (10, 25)


def test_missing_usage_and_model_version_fall_back() -> None:
    response = sdk_response(types.Part(text="hi"), model_version=None)
    response.usage_metadata = None
    result = from_gemini_response(response, requested_model=MODEL)
    assert (result.usage.input_tokens, result.usage.output_tokens) == (0, 0)
    assert result.model == MODEL


def test_one_tool_call_is_tool_use() -> None:
    result = from_gemini_response(
        sdk_response(fc("search_books", "c1", SIG_A, query="dune")), requested_model=MODEL
    )
    assert result.stop_reason is StopReason.TOOL_USE
    assert result.message.tool_calls == [
        ToolCallPart(
            id="c1",
            name="search_books",
            arguments={"query": "dune"},
            metadata={SIGNATURE_KEY: b64(SIG_A)},
        )
    ]


def test_parallel_calls_keep_order_and_signature_only_on_the_first() -> None:
    message = translate(
        sdk_response(
            types.Part(text="Looking up both."),
            fc("search_books", "c1", SIG_A, query="dune"),
            fc("search_books", "c2", None, query="ficciones"),
        )
    )
    assert isinstance(message.parts[0], TextPart)
    assert [(c.id, c.metadata) for c in message.tool_calls] == [
        ("c1", {SIGNATURE_KEY: b64(SIG_A)}),
        ("c2", None),
    ]


def test_missing_call_ids_are_generated_and_marked() -> None:
    calls = translate(sdk_response(fc("search", None), fc("search", None))).tool_calls
    assert len({c.id for c in calls}) == 2
    for call in calls:
        assert re.fullmatch(r"call_[0-9a-f]{24}", call.id)
        assert call.metadata == {GENERATED_ID_KEY: "1"}


def test_signatures_and_generated_ids_survive_a_round_trip() -> None:
    reply = translate(sdk_response(fc("search", None, SIG_A, query="dune")))
    call = reply.tool_calls[0]
    history = [
        Message.user("Find Dune"),
        reply,  # appended unchanged, as the agent loop must do
        Message.tool_results(ToolResultPart(tool_call_id=call.id, content="[]")),
    ]
    model_turn, results_turn = to_gemini_contents(history)[1:]
    assert model_turn.parts and results_turn.parts
    assert model_turn.parts[0].thought_signature == SIG_A
    assert model_turn.parts[0].function_call and model_turn.parts[0].function_call.id is None
    sent_result = results_turn.parts[0].function_response
    assert sent_result and sent_result.id is None and sent_result.name == "search"


def test_empty_text_part_carrying_a_signature_is_kept() -> None:
    message = translate(
        sdk_response(types.Part(text="Final answer."), types.Part(text="", thought_signature=SIG_B))
    )
    assert message.parts == (
        TextPart(text="Final answer."),
        TextPart(text="", metadata={SIGNATURE_KEY: b64(SIG_B)}),
    )
    assert message.text == "Final answer."


def test_thought_parts_are_skipped() -> None:
    message = translate(
        sdk_response(types.Part(text="secret reasoning", thought=True), types.Part(text="Answer"))
    )
    assert message.parts == (TextPart(text="Answer"),)


def test_no_finish_reason_with_text_is_end_turn() -> None:
    result = from_gemini_response(
        sdk_response(types.Part(text="hi"), finish=None), requested_model=MODEL
    )
    assert result.stop_reason is StopReason.END_TURN


def test_max_tokens_with_content() -> None:
    result = from_gemini_response(
        sdk_response(types.Part(text="Here are three bo"), finish=FR.MAX_TOKENS),
        requested_model=MODEL,
    )
    assert result.stop_reason is StopReason.MAX_TOKENS


def test_max_tokens_without_content_blames_thinking() -> None:
    with pytest.raises(LLMError) as error:
        from_gemini_response(sdk_response(finish=FR.MAX_TOKENS), requested_model=MODEL)
    assert not isinstance(error.value, LLMRefusalError)
    assert "max_tokens" in str(error.value)
    assert "thinking" in str(error.value)
    assert "GEMINI_THINKING_LEVEL" in str(error.value)


def test_max_tokens_with_only_a_signature_counts_as_no_content() -> None:
    with pytest.raises(LLMError, match="likely on thinking"):
        from_gemini_response(
            sdk_response(types.Part(text="", thought_signature=SIG_A), finish=FR.MAX_TOKENS),
            requested_model=MODEL,
        )


def test_stop_with_no_content_is_an_empty_response() -> None:
    with pytest.raises(LLMError, match="empty response"):
        from_gemini_response(sdk_response(finish=FR.STOP), requested_model=MODEL)


@pytest.mark.parametrize(
    "reason",
    [
        FR.SAFETY,
        FR.RECITATION,
        FR.BLOCKLIST,
        FR.PROHIBITED_CONTENT,
        FR.SPII,
        FR.MODEL_ARMOR,
        FR.IMAGE_SAFETY,
        FR.IMAGE_PROHIBITED_CONTENT,
        FR.IMAGE_RECITATION,
    ],
)
def test_refusal_reasons_raise_refusal_error(reason: types.FinishReason) -> None:
    with pytest.raises(LLMRefusalError) as error:
        from_gemini_response(sdk_response(finish=reason), requested_model=MODEL)
    assert error.value.reason == reason.value


@pytest.mark.parametrize(
    "reason",
    [
        FR.MALFORMED_FUNCTION_CALL,
        FR.MISSING_THOUGHT_SIGNATURE,
        FR.TOO_MANY_TOOL_CALLS,
        FR.UNEXPECTED_TOOL_CALL,
        FR.LANGUAGE,
        FR.OTHER,
        FR.FINISH_REASON_UNSPECIFIED,
    ],
)
def test_failure_reasons_raise_plain_llm_error(reason: types.FinishReason) -> None:
    with pytest.raises(LLMError, match=reason.value) as error:
        from_gemini_response(
            sdk_response(types.Part(text="partial"), finish=reason), requested_model=MODEL
        )
    assert not isinstance(error.value, LLMRefusalError)


def test_blocked_prompt_raises_refusal_error() -> None:
    response = types.GenerateContentResponse(
        prompt_feedback=types.GenerateContentResponsePromptFeedback(
            block_reason=types.BlockedReason.SAFETY
        )
    )
    with pytest.raises(LLMRefusalError, match="blocked the prompt") as error:
        from_gemini_response(response, requested_model=MODEL)
    assert error.value.reason == "SAFETY"


def test_no_candidates_raises() -> None:
    with pytest.raises(LLMError, match="no candidates"):
        from_gemini_response(types.GenerateContentResponse(), requested_model=MODEL)


# --- Errors -----------------------------------------------------------------------


def api_error_json(code: int, status: str, *details: dict[str, str]) -> dict[str, object]:
    return {"error": {"code": code, "message": "Something happened", "status": status,
                      "details": list(details)}}  # fmt: skip


def retry_info(delay: str) -> dict[str, str]:
    return {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}


@pytest.mark.parametrize(("delay", "seconds"), [("17s", 17.0), ("1.5s", 1.5)])
def test_429_with_retry_delay(delay: str, seconds: float) -> None:
    error = errors.ClientError(429, api_error_json(429, "RESOURCE_EXHAUSTED", retry_info(delay)))
    mapped = to_llm_error(error)
    assert isinstance(mapped, LLMRateLimitError)
    assert mapped.retry_after == seconds
    assert "429 RESOURCE_EXHAUSTED: Something happened" in str(mapped)


def test_429_without_retry_delay() -> None:
    error = errors.ClientError(429, api_error_json(429, "RESOURCE_EXHAUSTED"))
    mapped = to_llm_error(error)
    assert isinstance(mapped, LLMRateLimitError)
    assert mapped.retry_after is None


def test_402_resource_exhausted_is_not_a_rate_limit() -> None:
    mapped = to_llm_error(errors.ClientError(402, api_error_json(402, "RESOURCE_EXHAUSTED")))
    assert type(mapped) is LLMError


def test_server_error_maps_to_llm_error() -> None:
    mapped = to_llm_error(errors.ServerError(500, api_error_json(500, "INTERNAL")))
    assert type(mapped) is LLMError
    assert str(mapped) == "Gemini API error 500 INTERNAL: Something happened"


# --- complete(): the real SDK over a mock HTTP transport ---------------------------

API_KEY = "super-secret-test-key"
Handler = Callable[[httpx2.Request], httpx2.Response]


class Recorder:
    def __init__(self, *responses: httpx2.Response | Exception) -> None:
        self._responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        item = self._responses[min(len(self.requests), len(self._responses)) - 1]
        if isinstance(item, Exception):
            raise item
        return item


def gemini_client(handler: Handler) -> GeminiClient:
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    client = genai.Client(api_key=API_KEY, http_options=types.HttpOptions(httpx_async_client=http))
    return GeminiClient(client, model=MODEL, thinking_level="low")


OK_BODY = {
    "candidates": [
        {
            "content": {
                "role": "model",
                "parts": [{"text": "Hi!", "thoughtSignature": b64(SIG_A)}],
            },
            "finishReason": "STOP",
        }
    ],
    "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 2, "thoughtsTokenCount": 30},
    "modelVersion": "gemini-3.5-flash-lite-001",
}


async def test_complete_sends_the_request_and_parses_the_reply() -> None:
    recorder = Recorder(httpx2.Response(200, json=OK_BODY))
    result = await gemini_client(recorder).complete(
        system="Be brief.", messages=[Message.user("hello")], tools=[SEARCH], max_tokens=512
    )

    assert result.message.parts == (TextPart(text="Hi!", metadata={SIGNATURE_KEY: b64(SIG_A)}),)
    assert result.stop_reason is StopReason.END_TURN
    assert (result.usage.input_tokens, result.usage.output_tokens) == (7, 32)

    request = recorder.requests[0]
    assert request.url.path.endswith(f"models/{MODEL}:generateContent")
    assert request.headers["x-goog-api-key"] == API_KEY
    assert API_KEY not in str(request.url)
    body = json.loads(request.content)
    assert body["systemInstruction"]["parts"][0]["text"] == "Be brief."
    assert body["generationConfig"]["maxOutputTokens"] == 512
    # google-genai 3.3 sends this one key in snake_case; protobuf JSON accepts both spellings.
    thinking = body["generationConfig"]["thinkingConfig"]
    assert "LOW" in (thinking.get("thinkingLevel"), thinking.get("thinking_level"))
    assert body["tools"][0]["functionDeclarations"][0]["name"] == "search_books"


async def test_complete_maps_429_without_retrying() -> None:
    body = api_error_json(429, "RESOURCE_EXHAUSTED", retry_info("21s"))
    recorder = Recorder(httpx2.Response(429, json=body))
    with pytest.raises(LLMRateLimitError) as error:
        await gemini_client(recorder).complete(
            system="s", messages=[Message.user("hi")], max_tokens=10
        )
    assert error.value.retry_after == 21.0
    assert len(recorder.requests) == 1


async def test_complete_maps_server_errors_without_retrying() -> None:
    recorder = Recorder(httpx2.Response(503, json=api_error_json(503, "UNAVAILABLE")))
    with pytest.raises(LLMError, match="503 UNAVAILABLE"):
        await gemini_client(recorder).complete(
            system="s", messages=[Message.user("hi")], max_tokens=10
        )
    assert len(recorder.requests) == 1


async def test_complete_maps_network_errors() -> None:
    recorder = Recorder(httpx2.ConnectError("down"))
    with pytest.raises(LLMError, match="request failed: ConnectError"):
        await gemini_client(recorder).complete(
            system="s", messages=[Message.user("hi")], max_tokens=10
        )


async def test_errors_never_contain_the_api_key() -> None:
    recorder = Recorder(
        httpx2.Response(429, json=api_error_json(429, "RESOURCE_EXHAUSTED")),
        httpx2.Response(400, json=api_error_json(400, "INVALID_ARGUMENT")),
        httpx2.ConnectError("down"),
    )
    client = gemini_client(recorder)
    for _ in range(3):
        with pytest.raises(LLMError) as error:
            await client.complete(system="s", messages=[Message.user("hi")], max_tokens=10)
        assert API_KEY not in str(error.value)
