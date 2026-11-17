"""The agent loop: the model searches books and submits recommendations; code validates.

Each turn, the model may call search_books (Google Books) or submit_recommendations.
A submission is checked with validate_recommendations (count, existence, library,
filters, citations) and judged by the acceptance policy (domain/acceptance.py). A
rejected submission goes back to the model as an error result listing each problem, so
it can replace only the rejected books and submit again. Invalid arguments, unknown
tools and search failures also go back as error results, never as exceptions.

run_agent returns an AgentResult or raises AgentError, which carries what evals need
to explain a failure (the last validation result, the calls, the iterations).
"""

from time import perf_counter

from pydantic import BaseModel, ValidationError

from book_agent.domain.acceptance import is_acceptable
from book_agent.domain.models import Profile
from book_agent.domain.recommendations import Filters, RecommendationList, ValidationResult
from book_agent.domain.validation import WorkMatching, validate_recommendations
from book_agent.llm.base import (
    LLMClient,
    LLMResponse,
    Message,
    StopReason,
    ToolCallPart,
    ToolResultPart,
    Usage,
)
from book_agent.llm.calls import LLMCall, total_usage
from book_agent.ports import BookSearch, BookSearchError
from book_agent.prompts import submission_feedback_v1
from book_agent.same_work_judge import LLMSameWorkJudge
from book_agent.tools import (
    SEARCH_BOOKS,
    SUBMIT_RECOMMENDATIONS,
    SearchBooksArgs,
    run_search_books,
)

MAX_ITERATIONS = 6
MAX_TOKENS_PER_TURN = 2048
CALL_NAME = "agent"
TOOLS = [SEARCH_BOOKS, SUBMIT_RECOMMENDATIONS]
REMINDER = "Submit your answer by calling the submit_recommendations tool."


class AgentResult(BaseModel, frozen=True):
    """An accepted submission and what it took to get there."""

    recommendations: RecommendationList
    validation: ValidationResult  # of the accepted submission: resolved books, links, unknowns
    rejections: list[ValidationResult] = []  # each rejected submission's validation, in order
    calls: list[LLMCall]  # agent turns ("agent") and same-work judge calls, in order
    iterations: int  # LLM requests made by the loop
    feedback_prompt_id: str = submission_feedback_v1.PROMPT_ID

    @property
    def submissions(self) -> int:
        """Submissions that parsed and were validated: the rejected ones plus the accepted."""
        return len(self.rejections) + 1

    @property
    def total_usage(self) -> Usage | None:
        return total_usage(self.calls)


class AgentError(Exception):
    """The agent could not produce acceptable recommendations.

    `last_validation` is the last rejected submission's validation, if there was one, so
    the CLI and evals can show what went wrong.
    """

    def __init__(
        self,
        message: str,
        *,
        last_validation: ValidationResult | None = None,
        calls: list[LLMCall] | None = None,
        iterations: int = 0,
    ) -> None:
        super().__init__(message)
        self.last_validation = last_validation
        self.calls = list(calls or [])
        self.iterations = iterations


async def run_agent(
    *,
    system: str,
    user_prompt: str,
    llm: LLMClient,
    books: BookSearch,
    profile: Profile,
    filters: Filters,
    work_matching: WorkMatching | None = None,
) -> AgentResult:
    messages: list[Message] = [Message.user(user_prompt)]
    calls: list[LLMCall] = []
    rejections: list[ValidationResult] = []

    for iteration in range(1, MAX_ITERATIONS + 1):
        response = await _complete(llm, system, messages, calls)
        messages.append(response.message)  # unchanged: thought signatures live here

        if not response.message.tool_calls:
            if response.stop_reason == StopReason.MAX_TOKENS:
                raise AgentError(
                    "the model hit the token limit without calling a tool",
                    last_validation=rejections[-1] if rejections else None,
                    calls=calls,
                    iterations=iteration,
                )
            messages.append(Message.user(REMINDER))
            continue

        # A call cut off at MAX_TOKENS still runs: malformed arguments go back as an
        # error like any other invalid arguments (D-015).
        results: list[ToolResultPart] = []
        for call in response.message.tool_calls:
            if call.name == SEARCH_BOOKS.name:
                results.append(await _search(call, books))
            elif call.name == SUBMIT_RECOMMENDATIONS.name:
                try:
                    submitted = RecommendationList.model_validate(call.arguments)
                except ValidationError as e:
                    content = f"Invalid arguments for submit_recommendations: {describe(e)}"
                    results.append(error_result(call, content))
                    continue
                validation = await _validate(
                    submitted, profile, filters, books, work_matching, calls
                )
                if is_acceptable(validation):
                    return AgentResult(
                        recommendations=submitted,
                        validation=validation,
                        rejections=rejections,
                        calls=calls,
                        iterations=iteration,
                    )
                rejections.append(validation)
                feedback = submission_feedback_v1.build_feedback(
                    submitted.recommendations, validation
                )
                results.append(error_result(call, feedback))
            else:
                available = ", ".join(tool.name for tool in TOOLS)
                content = f"Unknown tool '{call.name}'. Available tools: {available}."
                results.append(error_result(call, content))

        messages.append(Message.tool_results(*results))

    rejected = f" ({len(rejections)} submissions rejected)" if rejections else ""
    raise AgentError(
        f"no accepted recommendations after {MAX_ITERATIONS} iterations{rejected}",
        last_validation=rejections[-1] if rejections else None,
        calls=calls,
        iterations=MAX_ITERATIONS,
    )


async def _complete(
    llm: LLMClient, system: str, messages: list[Message], calls: list[LLMCall]
) -> LLMResponse:
    started = perf_counter()
    response = await llm.complete(
        system=system, messages=messages, tools=TOOLS, max_tokens=MAX_TOKENS_PER_TURN
    )
    calls.append(
        LLMCall(
            name=CALL_NAME,
            model=response.model,
            usage=response.usage,
            latency_s=perf_counter() - started,
        )
    )
    return response


async def _search(call: ToolCallPart, books: BookSearch) -> ToolResultPart:
    try:
        args = SearchBooksArgs.model_validate(call.arguments)
        content = await run_search_books(args, books)
    except ValidationError as e:
        return error_result(call, f"Invalid arguments for search_books: {describe(e)}")
    except BookSearchError as e:
        return error_result(call, f"Book search failed: {e}")
    return ToolResultPart(tool_call_id=call.id, content=content)


async def _validate(
    submitted: RecommendationList,
    profile: Profile,
    filters: Filters,
    books: BookSearch,
    work_matching: WorkMatching | None,
    calls: list[LLMCall],
) -> ValidationResult:
    """Validate a submission, adding any same-work judge calls it made to `calls`."""
    judge = work_matching.judge if work_matching else None
    recorded = len(judge.calls) if isinstance(judge, LLMSameWorkJudge) else 0
    validation = await validate_recommendations(
        submitted.recommendations, profile, filters, books, work_matching
    )
    if isinstance(judge, LLMSameWorkJudge):
        calls += judge.calls[recorded:]
    return validation


def error_result(call: ToolCallPart, message: str) -> ToolResultPart:
    return ToolResultPart(tool_call_id=call.id, content=message, is_error=True)


def describe(error: ValidationError) -> str:
    """One compact line per invalid field, without pydantic's documentation links."""
    problems = (
        f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}"
        for issue in error.errors(include_url=False)
    )
    return "; ".join(problems)
