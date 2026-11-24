"""One end-to-end agent run: interpret the request, run the loop, collect the evidence.

The agent counterpart of baseline.run_baseline, with the same request interpretation
(request_parser.resolve_request) and the same validation, so their results compare
directly. run_agent_pipeline never raises for model, catalog or parsing problems: they end
up in AgentRun.error, so an eval run always gets one result per case.
"""

from typing import Literal

from pydantic import BaseModel

from book_agent.agent import AgentError, AgentFailure, TurnTrace, run_agent
from book_agent.domain.models import Profile
from book_agent.domain.recommendations import Filters, RecommendationList, ValidationResult
from book_agent.domain.requests import ResolvedRequest
from book_agent.domain.validation import WorkMatching
from book_agent.library_retrieval import query_text
from book_agent.llm.base import LLMClient, LLMError, Usage
from book_agent.llm.calls import LLMCall, total_usage
from book_agent.ports import BookSearch, LibraryRetriever, WorkResolver
from book_agent.prompts import agent_v1, agent_v2, agent_v3, agent_v4, submission_feedback_v1
from book_agent.prompts.agent_prompt import AgentPrompt
from book_agent.request_parser import resolve_request
from book_agent.same_work_judge import LLMSameWorkJudge

AGENT_PROMPTS: dict[str, AgentPrompt] = {
    prompt.prompt_id: prompt
    for prompt in (agent_v1.PROMPT, agent_v2.PROMPT, agent_v3.PROMPT, agent_v4.PROMPT)
}
DEFAULT_AGENT_PROMPT = agent_v3.PROMPT  # D-026: its user prompt states min_pages


class AgentRun(BaseModel, frozen=True):
    prompt_id: str
    feedback_prompt_id: str = submission_feedback_v1.PROMPT_ID
    request_prompt_id: str | None = None  # set when a request text was interpreted
    request_text: str | None = None
    request: ResolvedRequest | None = None  # None when interpretation failed
    # In order: interpret_request (optional), then agent turns and judge calls.
    calls: list[LLMCall] = []
    iterations: int = 0
    turns: list[TurnTrace] = []  # what each agent turn did: tool calls and outcomes
    repair_turn_used: bool = False  # a rejected last-turn submission got one extra turn
    rejections: list[ValidationResult] = []  # each rejected submission, in order
    recommendations: RecommendationList | None = None  # the accepted submission
    validation: ValidationResult | None = None  # of the accepted submission
    last_validation: ValidationResult | None = None  # on failure: the last one validated
    raw_text: str | None = None  # the interpreter's reply when it couldn't be parsed
    failure: AgentFailure | None = None  # None for request errors and successes
    # Why the request couldn't be acted on: "interpretation" or "contradiction" (D-026).
    request_error_kind: Literal["interpretation", "contradiction"] | None = None
    error: str | None = None
    # Library retrieval (D-041): the read books shown to the agent, most related first, and
    # why none were shown when retrieval was attempted and failed.
    retrieved: list[str] = []
    retrieval_error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and self.validation is not None

    @property
    def submissions(self) -> int:
        """Validated submissions: the rejected ones plus the accepted one, if any."""
        return len(self.rejections) + (1 if self.validation is not None else 0)

    @property
    def total_usage(self) -> Usage | None:
        return total_usage(self.calls)


async def run_agent_pipeline(
    profile: Profile,
    flags: Filters,
    *,
    request: str | None = None,
    llm: LLMClient,
    books: BookSearch,
    works: WorkResolver | None = None,
    prompt: AgentPrompt = DEFAULT_AGENT_PROMPT,
    retriever: LibraryRetriever | None = None,
) -> AgentRun:
    """Interpret the optional request, merge its filters with `flags`, run the agent.

    A failed interpretation stops the run before the agent starts (D-007). Same-work
    checks use the same LLM; with `works`, equal work keys settle them first. `prompt` picks
    the agent prompt version (system prompt and tool descriptions), agent_v3 by default.
    A prompt that uses retrieval needs `retriever`; requests without text get no retrieval,
    and a failed retrieval falls back to the prompt without related books (D-041).
    """
    if prompt.uses_retrieval and retriever is None:
        raise ValueError(f"{prompt.prompt_id} needs a library retriever")
    run = AgentRun(prompt_id=prompt.prompt_id)
    resolution = await resolve_request(request, flags, llm=llm)
    interpretation = resolution.interpretation
    if interpretation is not None:
        run = run.model_copy(
            update={
                "request_prompt_id": interpretation.prompt_id,
                "request_text": request.strip() if request else None,
                "calls": [interpretation.call] if interpretation.call else [],
            }
        )
    if resolution.request is None:
        assert interpretation is not None  # only text that can't be acted on gives no request
        return run.model_copy(
            update={
                "raw_text": interpretation.raw_text,
                "error": resolution.error,
                "request_error_kind": resolution.error_kind,
            }
        )
    resolved = resolution.request
    run = run.model_copy(update={"request": resolved})

    user_prompt = prompt.build_user_prompt(profile, resolved.filters, resolved.preference)
    if prompt.build_related_prompt is not None and retriever is not None and resolved.text:
        try:
            related = await retriever.related_books(query_text(resolved.text, resolved.preference))
        except LLMError as e:
            run = run.model_copy(update={"retrieval_error": str(e)})
        else:
            user_prompt = prompt.build_related_prompt(
                profile, resolved.filters, resolved.preference, related
            )
            run = run.model_copy(update={"retrieved": [b.title for b in related]})

    try:
        result = await run_agent(
            system=prompt.system,
            user_prompt=user_prompt,
            tools=prompt.tools,
            llm=llm,
            books=books,
            profile=profile,
            filters=resolved.filters,
            work_matching=WorkMatching(judge=LLMSameWorkJudge(llm), resolver=works),
        )
    except AgentError as e:
        return run.model_copy(
            update={
                "calls": [*run.calls, *e.calls],
                "iterations": e.iterations,
                "turns": e.turns,
                "repair_turn_used": e.repair_turn_used,
                "rejections": e.rejections,
                "last_validation": e.last_validation,
                "failure": e.failure,
                "error": str(e),
            }
        )
    return run.model_copy(
        update={
            "calls": [*run.calls, *result.calls],
            "iterations": result.iterations,
            "turns": result.turns,
            "repair_turn_used": result.repair_turn_used,
            "rejections": result.rejections,
            "recommendations": result.recommendations,
            "validation": result.validation,
        }
    )
