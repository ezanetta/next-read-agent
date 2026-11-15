"""Live tests against the real Gemini API. Excluded by default.

Run with: uv run pytest -m live tests/test_gemini_live.py -s
Uses GEMINI_API_KEY, GEMINI_MODEL and GEMINI_THINKING_LEVEL from .env.

Kept minimal because every run uses free-tier quota: about 3 requests in total
(1 for the text reply, 2 for the tool round trip). Parallel calls are covered by unit tests.
"""

from collections.abc import Callable

import pytest
from google import genai
from pydantic import BaseModel, Field, ValidationError

from book_agent.config import GeminiThinkingLevel, get_settings
from book_agent.llm.base import (
    JsonObject,
    LLMResponse,
    Message,
    StopReason,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
)
from book_agent.llm.gemini import SIGNATURE_KEY, GeminiClient

pytestmark = pytest.mark.live

PAGE_COUNTS = {"dune": 896, "ficciones": 174}


class PageCountArgs(BaseModel):
    title: str = Field(description="The book title, e.g. 'Dune'")


PAGE_COUNT_TOOL = ToolSpec.from_model(
    "get_page_count", "Return the page count of a book by its title.", PageCountArgs
)
SYSTEM = "You answer questions about books. Use the tools for facts; never guess page counts."


GeminiFactory = Callable[[GeminiThinkingLevel | None], GeminiClient]


@pytest.fixture
def make_llm() -> GeminiFactory:
    """Build a client for the configured model with an explicit thinking level."""
    try:
        settings = get_settings()
    except ValidationError:
        pytest.skip("GEMINI_API_KEY is not configured")
    client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())

    def make(thinking_level: GeminiThinkingLevel | None) -> GeminiClient:
        return GeminiClient(client, model=settings.gemini_model, thinking_level=thinking_level)

    return make


def run_tool(arguments: JsonObject) -> str:
    title = str(PageCountArgs.model_validate(arguments).title).casefold()
    pages = next((p for t, p in PAGE_COUNTS.items() if t in title), None)
    return str(pages) if pages else "unknown title"


def signatures_in(message: Message) -> list[str]:
    """Describe which parts of a message carry a Gemini thought signature."""
    return [
        f"{type(p).__name__}#{i}"
        for i, p in enumerate(message.parts)
        if isinstance(p, TextPart | ToolCallPart) and p.metadata and SIGNATURE_KEY in p.metadata
    ]


async def test_live_plain_text_reply(make_llm: GeminiFactory) -> None:
    llm = make_llm(None)  # the model's default thinking level
    result = await llm.complete(
        system="Answer in one short sentence.",
        messages=[Message.user("Who wrote Dune?")],
        max_tokens=2048,
    )
    print(f"\n{result.model}: {result.message.text!r} {result.usage}")
    assert result.stop_reason is StopReason.END_TURN
    assert "herbert" in result.message.text.casefold()
    assert result.usage.input_tokens > 0
    assert result.usage.output_tokens > 0


async def test_live_tool_round_trip(make_llm: GeminiFactory) -> None:
    """Proves signatures survive the round trip and that thinking_level is accepted.

    Uses an explicit "low" level: the default ("minimal" on Flash-Lite) may produce no
    signatures, which would let this test pass without proving anything. A 200 response
    also shows the API accepts the SDK's snake_case `thinking_level` key.
    """
    llm = make_llm("low")
    history = [Message.user("How many pages does Dune have?")]
    signatures: list[str] = []
    result: LLMResponse | None = None
    for lap in range(1, 4):  # normally 2 laps: the tool call, then the answer
        result = await llm.complete(
            system=SYSTEM, messages=history, tools=[PAGE_COUNT_TOOL], max_tokens=2048
        )
        history.append(result.message)  # unchanged, as the agent loop must do
        calls = result.message.tool_calls
        found = signatures_in(result.message)
        signatures += [f"lap {lap}: {s}" for s in found]
        print(
            f"\nlap {lap} {result.stop_reason}: {[(c.name, c.arguments) for c in calls]} "
            f"signatures={found} {result.usage}"
        )
        if result.stop_reason is not StopReason.TOOL_USE:
            break
        history.append(
            Message.tool_results(
                *(ToolResultPart(tool_call_id=c.id, content=run_tool(c.arguments)) for c in calls)
            )
        )

    assert result is not None
    print(f"final: {result.message.text!r}")
    print(f"thought signatures received: {signatures or 'NONE'}")
    assert result.stop_reason is StopReason.END_TURN
    assert "896" in result.message.text
    assert signatures, (
        "The round trip worked but no thought signature arrived, so it proves nothing "
        "about signature handling. Try a higher thinking level or another model."
    )
