"""The agent loop.

Retrieves candidates, lets the LLM rerank them and call tools, and validates the hard
rules (three books, none already read, filters respected, valid links) before accepting
a recommendation. Not implemented yet.
"""
from pydantic import ValidationError

from book_agent.domain.recommendations import RecommendationList
from book_agent.llm.base import (
    LLMClient,
    Message,
    StopReason,
    ToolResultPart, ToolCallPart,
)
from book_agent.ports import BookSearch, BookSearchError
from book_agent.tools import (
    SEARCH_BOOKS,
    SUBMIT_RECOMMENDATIONS,
    SearchBooksArgs,
    run_search_books,
)

MAX_ITERATIONS = 6
TOOLS = [SEARCH_BOOKS, SUBMIT_RECOMMENDATIONS]


class AgentError(Exception):
    """The agent could not produce recommendations."""


async def run_agent(
    *, system: str, user_prompt: str, llm: LLMClient, books: BookSearch
) -> RecommendationList:
    messages: list[Message] = [Message.user(user_prompt)]

    for _ in range(MAX_ITERATIONS):
        response = await llm.complete(
            system=system, messages=messages, tools=TOOLS, max_tokens=2048
        )
        messages.append(response.message)  # unchanged: thought signatures live here

        if not response.message.tool_calls:
            if response.stop_reason == StopReason.MAX_TOKENS:
                raise AgentError("the model hit the token limit without calling a tool")
            messages.append(
                Message.user("Submit your answer by calling the submit_recommendations tool.")
            )
            continue

        results: list[ToolResultPart] = []
        for call in response.message.tool_calls:
            if call.name == SEARCH_BOOKS.name:
                try:
                    args = SearchBooksArgs.model_validate(call.arguments)
                    content = await run_search_books(args, books)
                except ValidationError as e:
                    content = f"Invalid arguments for search_books: {describe(e)}"
                    results.append(
                        error_result(call, content)
                    )
                    continue
                except BookSearchError as e:
                    content = f"Book search failed: {e}"
                    results.append(
                        error_result(call, content)
                    )
                    continue
                results.append(ToolResultPart(tool_call_id=call.id, content=content))
            elif call.name == SUBMIT_RECOMMENDATIONS.name:
                try:
                    return RecommendationList.model_validate(call.arguments)
                except ValidationError as e:
                    content = f"Invalid arguments for submit_recommendations: {describe(e)}"
                    results.append(
                        error_result(call, content)
                    )
            else:
                available = ", ".join(tool.name for tool in TOOLS)
                content = f"Unknown tool '{call.name}'. Available tools: {available}."
                results.append(
                    error_result(call, content)
                )

        messages.append(Message.tool_results(*results))

    raise AgentError(f"no recommendations after {MAX_ITERATIONS} iterations")

def error_result(call: ToolCallPart, message: str) -> ToolResultPart:
    return ToolResultPart(tool_call_id=call.id, content=message, is_error=True)

def describe(error: ValidationError) -> str:
    """One compact line per invalid field, without pydantic's documentation links."""
    problems = (
        f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}"
        for issue in error.errors(include_url=False)
    )
    return "; ".join(problems)
