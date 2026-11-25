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

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from time import perf_counter

from pydantic import BaseModel, ValidationError

from book_agent.agent_events import (
    AgentEvent,
    EventSink,
    SubmissionChecked,
    SubmissionChecking,
    TurnFinished,
)
from book_agent.domain.acceptance import (
    blocking_problems,
    infrastructure_failures,
    is_acceptable,
    rejected_indexes,
)
from book_agent.domain.models import CatalogBook, Profile
from book_agent.domain.recommendations import (
    EXPECTED_RECOMMENDATIONS,
    Filters,
    RecommendationList,
    ValidationResult,
)
from book_agent.domain.validation import WorkMatching, validate_recommendations
from book_agent.llm.base import (
    JsonObject,
    LLMClient,
    LLMError,
    LLMResponse,
    Message,
    StopReason,
    TextPart,
    ToolCallPart,
    ToolChoice,
    ToolResultPart,
    ToolSpec,
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

MAX_ITERATIONS = 6  # regular turns
REPAIR_TURNS = 1  # extra turns, only after a submission on the last regular turn is rejected
MAX_TURNS = MAX_ITERATIONS + REPAIR_TURNS  # the effective cap on LLM requests (D-020)
MAX_TOKENS_PER_TURN = 2048
CALL_NAME = "agent"
REMINDER = "Submit your answer by calling the submit_recommendations tool."
NOTICE_TURNS_LEFT = 2  # the submit-now notice goes out when this many turns are left
SUBMIT_NOW_NOTICE = (
    f"You have {NOTICE_TURNS_LEFT} turns left. Call submit_recommendations now with your "
    f"{EXPECTED_RECOMMENDATIONS} best books: if a book is rejected, you can still fix it on "
    "your last turn."
)
"""Sent with the message that closes the turn before the last two, for every prompt
version (D-018, revised by D-028): late submission left no turn to fix a rejection."""
FORCED_SUBMIT = ToolChoice.required(SUBMIT_RECOMMENDATIONS)
"""The tool choice on the last regular turn and the repair turn (D-028): the model must
submit, so a search can't use up the turn (F-012)."""
REPAIR_NOTICE = (
    "Your submission was rejected on your last turn. You get one extra turn to fix it: "
    "keep the accepted books, replace only the rejected ones, and call "
    "submit_recommendations now. There are no turns left for searching."
)
"""Sent after the rejection feedback when a submission on the last regular turn is
rejected, so every rejection gets one chance to be fixed (D-020, F-007)."""


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
    submit_notice: bool = False  # the message closing this turn carried SUBMIT_NOW_NOTICE
    forced_tool: str | None = None  # the tool this turn's request forced, if any
    repair_turn_granted: bool = False  # ... and REPAIR_NOTICE: a repair turn follows
    llm_retries: int = 0  # this turn's request was resent after a server error (D-030)


class AgentResult(BaseModel, frozen=True):
    """An accepted submission and what it took to get there."""

    recommendations: RecommendationList
    validation: ValidationResult  # of the accepted submission: resolved books, links, unknowns
    rejections: list[ValidationResult] = []  # each rejected submission's validation, in order
    calls: list[LLMCall]  # agent turns ("agent") and same-work judge calls, in order
    iterations: int  # LLM requests made by the loop
    turns: list[TurnTrace] = []  # one per completed LLM request, in order
    repair_turn_used: bool = False  # the accepted submission came in the repair turn
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
        repair_turn_used: bool = False,
    ) -> None:
        super().__init__(message)
        self.failure = failure
        self.rejections = list(rejections or [])
        self.last_validation = last_validation
        self.calls = list(calls or [])
        self.iterations = iterations
        self.turns = list(turns or [])
        self.repair_turn_used = repair_turn_used


@dataclass
class _RunState:
    """What a run accumulates; builds the AgentError that carries it."""

    calls: list[LLMCall] = field(default_factory=list[LLMCall])
    rejections: list[ValidationResult] = field(default_factory=list[ValidationResult])
    turns: list[TurnTrace] = field(default_factory=list[TurnTrace])
    repair_turn_used: bool = False
    on_event: EventSink | None = None

    def emit(self, event: AgentEvent) -> None:
        if self.on_event is not None:
            self.on_event(event)

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
            repair_turn_used=self.repair_turn_used,
        )

    def record_turn(
        self,
        iteration: int,
        response: LLMResponse,
        tools: list[ToolTrace],
        note: str | None = None,
        submit_notice: bool = False,
        repair_turn_granted: bool = False,
        forced: bool = False,
    ) -> None:
        self.turns.append(
            TurnTrace(
                iteration=iteration,
                stop_reason=response.stop_reason,
                output_tokens=response.usage.output_tokens,
                text=response.message.text,
                tools=list(tools),
                note=note,
                submit_notice=submit_notice,
                repair_turn_granted=repair_turn_granted,
                forced_tool=SUBMIT_RECOMMENDATIONS if forced else None,
                llm_retries=response.retries,
            )
        )
        self.emit(TurnFinished(self.turns[-1]))


async def run_agent(
    *,
    system: str,
    user_prompt: str,
    tools: Sequence[ToolSpec],
    llm: LLMClient,
    books: BookSearch,
    profile: Profile,
    filters: Filters,
    work_matching: WorkMatching | None = None,
    on_event: EventSink | None = None,
) -> AgentResult:
    messages: list[Message] = [Message.user(user_prompt)]
    state = _RunState(on_event=on_event)

    limit = MAX_ITERATIONS  # raised to MAX_TURNS when the repair turn is granted
    iteration = 0
    while iteration < limit:
        iteration += 1
        # The last regular turn and the repair turn must submit (D-028).
        forced = iteration >= MAX_ITERATIONS
        try:
            response = await _complete(
                llm, system, tools, messages, state.calls, FORCED_SUBMIT if forced else None
            )
        except LLMError as e:
            raise state.error(f"LLM call failed: {e}", AgentFailure.LLM_ERROR, iteration) from e
        messages.append(response.message)  # unchanged: thought signatures live here

        if not response.message.tool_calls:
            if response.stop_reason == StopReason.MAX_TOKENS:
                state.record_turn(
                    iteration, response, [], note="no tool call: token limit", forced=forced
                )
                raise state.error(
                    "the model hit the token limit without calling a tool",
                    AgentFailure.TOKEN_LIMIT,
                    iteration,
                )
            notice = iteration == MAX_ITERATIONS - NOTICE_TURNS_LEFT
            state.record_turn(
                iteration,
                response,
                [],
                note="no tool call: reminder sent",
                submit_notice=notice,
                forced=forced,
            )
            messages.append(_closing_message([TextPart(text=REMINDER)], submit_notice=notice))
            continue

        # A call cut off at MAX_TOKENS still runs: malformed arguments go back as an
        # error like any other invalid arguments (D-015).
        results: list[ToolResultPart] = []
        traces: list[ToolTrace] = []
        rejected_this_turn = False
        for call in response.message.tool_calls:
            if forced and call.name not in FORCED_SUBMIT.names:
                # The provider should prevent this; never run another tool on a forced turn.
                content = (
                    f"Only submit_recommendations can be called on this turn; {call.name} "
                    "was not run."
                )
                results.append(error_result(call, content))
                traces.append(_trace(call, "not run: this turn had to submit", is_error=True))
                continue
            if call.name == SEARCH_BOOKS:
                result, outcome = await _search(call, books)
                results.append(result)
                traces.append(_trace(call, outcome, result.is_error))
                continue
            if call.name != SUBMIT_RECOMMENDATIONS:
                available = ", ".join(tool.name for tool in tools)
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

            titles = [r.title for r in submitted.recommendations]
            state.emit(SubmissionChecking(iteration, titles))
            validation = await _validate(
                submitted, profile, filters, books, work_matching, state.calls
            )
            state.emit(SubmissionChecked(iteration, validation, is_acceptable(validation)))
            outage = infrastructure_failures(validation)
            if outage:  # the model can't fix an outage: stop instead of asking it to
                outcome = f"catalog unavailable: {len(outage)} lookups failed"
                traces.append(_trace(call, outcome, is_error=True))
                state.record_turn(iteration, response, traces, forced=forced)
                raise state.error(
                    f"the book catalog could not be reached ({len(outage)} lookups "
                    f"failed, first: {outage[0].message})",
                    AgentFailure.CATALOG_UNAVAILABLE,
                    iteration,
                    last_validation=validation,
                )
            if is_acceptable(validation):
                traces.append(_trace(call, "accepted"))
                state.record_turn(iteration, response, traces, forced=forced)
                return AgentResult(
                    recommendations=submitted,
                    validation=validation,
                    rejections=state.rejections,
                    calls=state.calls,
                    iterations=iteration,
                    turns=state.turns,
                    repair_turn_used=state.repair_turn_used,
                )
            state.rejections.append(validation)
            rejected_this_turn = True
            feedback = submission_feedback_v1.build_feedback(submitted.recommendations, validation)
            results.append(error_result(call, feedback))
            traces.append(_trace(call, _rejection_outcome(validation), is_error=True))

        notice = iteration == MAX_ITERATIONS - NOTICE_TURNS_LEFT
        # Only a rejection on the last regular turn earns the repair turn: earlier ones
        # still have a turn left, and a rejection in the repair turn ends the run.
        repair = rejected_this_turn and iteration == MAX_ITERATIONS
        if repair:
            limit = MAX_TURNS
            state.repair_turn_used = True
        state.record_turn(
            iteration,
            response,
            traces,
            submit_notice=notice,
            repair_turn_granted=repair,
            forced=forced,
        )
        messages.append(_closing_message(results, submit_notice=notice, repair=repair))

    rejected = f" ({len(state.rejections)} submissions rejected)" if state.rejections else ""
    repair_turn = f" including {REPAIR_TURNS} repair turn" if state.repair_turn_used else ""
    raise state.error(
        f"no accepted recommendations after {iteration} iterations{repair_turn}{rejected}",
        AgentFailure.ITERATIONS_EXHAUSTED,
        iteration,
    )


def _closing_message(
    parts: Sequence[ToolResultPart | TextPart],
    *,
    submit_notice: bool = False,
    repair: bool = False,
) -> Message:
    """The user message that ends a turn, with the submit-now or repair notice when due.

    Notices go last, after any tool results, in the same message: two user messages in a
    row are avoided, and Claude requires tool results before text (D-018).
    """
    notices = [SUBMIT_NOW_NOTICE] if submit_notice else []
    notices += [REPAIR_NOTICE] if repair else []
    return Message(role="user", parts=(*parts, *(TextPart(text=n) for n in notices)))


async def _complete(
    llm: LLMClient,
    system: str,
    tools: Sequence[ToolSpec],
    messages: list[Message],
    calls: list[LLMCall],
    tool_choice: ToolChoice | None = None,
) -> LLMResponse:
    started = perf_counter()
    try:
        response = await llm.complete(
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=MAX_TOKENS_PER_TURN,
            tool_choice=tool_choice,
        )
    except LLMError as e:
        calls.append(LLMCall.failed(CALL_NAME, e, started))
        raise
    calls.append(LLMCall.succeeded(CALL_NAME, response, started))
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
