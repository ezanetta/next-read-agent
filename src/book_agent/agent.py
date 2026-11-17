"""The agent loop: the model searches books and submits recommendations; code validates.

Each turn, the model may call search_books (Google Books) or submit_recommendations.
A submission is checked with validate_recommendations (count, existence, library,
filters, citations) and judged by the acceptance policy (domain/acceptance.py). A
rejected submission goes back to the model as an error result listing each problem, so
it can replace only the rejected books and submit again. A failed catalog lookup during
validation stops the run instead: the model can't fix an outage. Invalid arguments, unknown
tools and search failures also go back as error results, never as exceptions.

run_agent returns an AgentResult or raises AgentError, which carries what evals need
to explain a failure (the last validation result, the calls, the iterations).
"""

from dataclasses import dataclass, field
from enum import StrEnum
from time import perf_counter

from pydantic import BaseModel, ValidationError

from book_agent.domain.acceptance import (
    blocking_problems,
    infrastructure_failures,
    is_acceptable,
    rejected_indexes,
)
from book_agent.domain.models import CatalogBook, Profile
from book_agent.domain.recommendations import Filters, RecommendationList, ValidationResult
from book_agent.domain.validation import WorkMatching, validate_recommendations
from book_agent.llm.base import (
    JsonObject,
    LLMClient,
    LLMError,
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
from book_agent.pydantic_errors import describe
from book_agent.same_work_judge import LLMSameWorkJudge
from book_agent.tools import (
    SEARCH_BOOKS,
    SUBMIT_RECOMMENDATIONS,
    SearchBooksArgs,
    find_books,
    format_search_results,
)

MAX_ITERATIONS = 6
MAX_TOKENS_PER_TURN = 2048
CALL_NAME = "agent"
TOOLS = [SEARCH_BOOKS, SUBMIT_RECOMMENDATIONS]
REMINDER = "Submit your answer by calling the submit_recommendations tool."


class ToolTrace(BaseModel, frozen=True):
    """One tool call in a turn and a one-line summary of what came of it."""

    name: str
    arguments: JsonObject
    outcome: str  # e.g. "5 books: Dune; Hyperion", "no results", "rejected: book 1 (...)"
    is_error: bool = False


class TurnTrace(BaseModel, frozen=True):
    """What happened in one turn, for --verbose and for evals."""

    iteration: int
    stop_reason: StopReason
    output_tokens: int
    text: str  # what the model wrote besides tool calls; often empty
    tools: list[ToolTrace] = []
    note: str | None = None  # for turns without tool calls


class AgentResult(BaseModel, frozen=True):
    """An accepted submission and what it took to get there."""

    recommendations: RecommendationList
    validation: ValidationResult  # of the accepted submission: resolved books, links, unknowns
    rejections: list[ValidationResult] = []  # each rejected submission's validation, in order
    calls: list[LLMCall]  # agent turns ("agent") and same-work judge calls, in order
    iterations: int  # LLM requests made by the loop
    turns: list[TurnTrace] = []  # one per completed LLM request, in order
    feedback_prompt_id: str = submission_feedback_v1.PROMPT_ID

    @property
    def submissions(self) -> int:
        """Submissions that parsed and were validated: the rejected ones plus the accepted."""
        return len(self.rejections) + 1

    @property
    def total_usage(self) -> Usage | None:
        return total_usage(self.calls)


class AgentFailure(StrEnum):
    """Why a run produced no recommendations, so the CLI and evals can tell them apart."""

    ITERATIONS_EXHAUSTED = "iterations_exhausted"  # no accepted submission in time
    TOKEN_LIMIT = "token_limit"  # a reply was cut off without calling a tool
    CATALOG_UNAVAILABLE = "catalog_unavailable"  # validation couldn't look books up
    LLM_ERROR = "llm_error"  # a request to the model failed (rate limit, refusal, outage)


class AgentError(Exception):
    """The agent could not produce acceptable recommendations.

    Carries the evidence the CLI and evals need: why it failed, every rejected
    submission's validation, the last validated submission (rejected, or the one whose
    lookups failed), the calls and the iterations.
    """

    def __init__(
        self,
        message: str,
        *,
        failure: AgentFailure,
        rejections: list[ValidationResult] | None = None,
        last_validation: ValidationResult | None = None,
        calls: list[LLMCall] | None = None,
        iterations: int = 0,
        turns: list[TurnTrace] | None = None,
    ) -> None:
        super().__init__(message)
        self.failure = failure
        self.rejections = list(rejections or [])
        self.last_validation = last_validation
        self.calls = list(calls or [])
        self.iterations = iterations
        self.turns = list(turns or [])


@dataclass
class _RunState:
    """What a run accumulates; builds the AgentError that carries it."""

    calls: list[LLMCall] = field(default_factory=list[LLMCall])
    rejections: list[ValidationResult] = field(default_factory=list[ValidationResult])
    turns: list[TurnTrace] = field(default_factory=list[TurnTrace])

    def error(
        self,
        message: str,
        failure: AgentFailure,
        iterations: int,
        last_validation: ValidationResult | None = None,
    ) -> AgentError:
        return AgentError(
            message,
            failure=failure,
            rejections=self.rejections,
            last_validation=last_validation or (self.rejections[-1] if self.rejections else None),
            calls=self.calls,
            iterations=iterations,
            turns=self.turns,
        )

    def record_turn(
        self,
        iteration: int,
        response: LLMResponse,
        tools: list[ToolTrace],
        note: str | None = None,
    ) -> None:
        self.turns.append(
            TurnTrace(
                iteration=iteration,
                stop_reason=response.stop_reason,
                output_tokens=response.usage.output_tokens,
                text=response.message.text,
                tools=list(tools),
                note=note,
            )
        )


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
    state = _RunState()

    for iteration in range(1, MAX_ITERATIONS + 1):
        try:
            response = await _complete(llm, system, messages, state.calls)
        except LLMError as e:
            raise state.error(f"LLM call failed: {e}", AgentFailure.LLM_ERROR, iteration) from e
        messages.append(response.message)  # unchanged: thought signatures live here

        if not response.message.tool_calls:
            if response.stop_reason == StopReason.MAX_TOKENS:
                state.record_turn(iteration, response, [], note="no tool call: token limit")
                raise state.error(
                    "the model hit the token limit without calling a tool",
                    AgentFailure.TOKEN_LIMIT,
                    iteration,
                )
            state.record_turn(iteration, response, [], note="no tool call: reminder sent")
            messages.append(Message.user(REMINDER))
            continue

        # A call cut off at MAX_TOKENS still runs: malformed arguments go back as an
        # error like any other invalid arguments (D-015).
        results: list[ToolResultPart] = []
        traces: list[ToolTrace] = []
        for call in response.message.tool_calls:
            if call.name == SEARCH_BOOKS.name:
                result, outcome = await _search(call, books)
                results.append(result)
                traces.append(_trace(call, outcome, result.is_error))
                continue
            if call.name != SUBMIT_RECOMMENDATIONS.name:
                available = ", ".join(tool.name for tool in TOOLS)
                content = f"Unknown tool '{call.name}'. Available tools: {available}."
                results.append(error_result(call, content))
                traces.append(_trace(call, "unknown tool", is_error=True))
                continue
            try:
                submitted = RecommendationList.model_validate(call.arguments)
            except ValidationError as e:
                problems = describe(e)
                content = f"Invalid arguments for submit_recommendations: {problems}"
                results.append(error_result(call, content))
                traces.append(_trace(call, f"invalid arguments: {problems}", is_error=True))
                continue

            validation = await _validate(
                submitted, profile, filters, books, work_matching, state.calls
            )
            outage = infrastructure_failures(validation)
            if outage:  # the model can't fix an outage: stop instead of asking it to
                outcome = f"catalog unavailable: {len(outage)} lookups failed"
                traces.append(_trace(call, outcome, is_error=True))
                state.record_turn(iteration, response, traces)
                raise state.error(
                    f"the book catalog could not be reached ({len(outage)} lookups "
                    f"failed, first: {outage[0].message})",
                    AgentFailure.CATALOG_UNAVAILABLE,
                    iteration,
                    last_validation=validation,
                )
            if is_acceptable(validation):
                traces.append(_trace(call, "accepted"))
                state.record_turn(iteration, response, traces)
                return AgentResult(
                    recommendations=submitted,
                    validation=validation,
                    rejections=state.rejections,
                    calls=state.calls,
                    iterations=iteration,
                    turns=state.turns,
                )
            state.rejections.append(validation)
            feedback = submission_feedback_v1.build_feedback(submitted.recommendations, validation)
            results.append(error_result(call, feedback))
            traces.append(_trace(call, _rejection_outcome(validation), is_error=True))

        state.record_turn(iteration, response, traces)
        messages.append(Message.tool_results(*results))

    rejected = f" ({len(state.rejections)} submissions rejected)" if state.rejections else ""
    raise state.error(
        f"no accepted recommendations after {MAX_ITERATIONS} iterations{rejected}",
        AgentFailure.ITERATIONS_EXHAUSTED,
        MAX_ITERATIONS,
    )


async def _complete(
    llm: LLMClient, system: str, messages: list[Message], calls: list[LLMCall]
) -> LLMResponse:
    started = perf_counter()
    try:
        response = await llm.complete(
            system=system, messages=messages, tools=TOOLS, max_tokens=MAX_TOKENS_PER_TURN
        )
    except LLMError:
        calls.append(
            LLMCall(name=CALL_NAME, model=None, usage=None, latency_s=perf_counter() - started)
        )
        raise
    calls.append(
        LLMCall(
            name=CALL_NAME,
            model=response.model,
            usage=response.usage,
            latency_s=perf_counter() - started,
        )
    )
    return response


async def _search(call: ToolCallPart, books: BookSearch) -> tuple[ToolResultPart, str]:
    """The tool result for a search_books call and a one-line outcome for the trace."""
    try:
        args = SearchBooksArgs.model_validate(call.arguments)
        found = await find_books(args, books)
    except ValidationError as e:
        message = f"Invalid arguments for search_books: {describe(e)}"
        return error_result(call, message), f"invalid arguments: {describe(e)}"
    except BookSearchError as e:
        return error_result(call, f"Book search failed: {e}"), f"search failed: {e}"
    result = ToolResultPart(tool_call_id=call.id, content=format_search_results(args, found))
    return result, _search_outcome(found)


def _search_outcome(found: list[CatalogBook]) -> str:
    if not found:
        return "no results"
    titles = "; ".join(_shorten(b.title) for b in found)
    return f"{len(found)} book{'s' if len(found) > 1 else ''}: {titles}"


def _rejection_outcome(validation: ValidationResult) -> str:
    rules = ", ".join(sorted({p.rule.value for p in blocking_problems(validation)}))
    rejected = rejected_indexes(validation)
    if not rejected:
        return f"rejected: the list ({rules})"
    books = ", ".join(str(i + 1) for i in rejected)
    return f"rejected: book{'s' if len(rejected) > 1 else ''} {books} ({rules})"


def _trace(call: ToolCallPart, outcome: str, is_error: bool = False) -> ToolTrace:
    return ToolTrace(name=call.name, arguments=call.arguments, outcome=outcome, is_error=is_error)


def _shorten(text: str, limit: int = 40) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


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
