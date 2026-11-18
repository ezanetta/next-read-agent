import json

from book_agent.agent import CALL_NAME, AgentFailure
from book_agent.agent_run import AGENT_PROMPTS, DEFAULT_AGENT_PROMPT, run_agent_pipeline
from book_agent.domain.recommendations import Filters, Recommendation
from book_agent.domain.requests import FilterSource
from book_agent.llm.base import LLMError, LLMResponse, ToolCallPart
from book_agent.prompts import agent_v1, agent_v3, request_parser_v3
from tests.fakes import FakeLLM, text_response, tool_call_response
from tests.library_data import CLEAN_THREE, PROFILE, catalog, rec


def submit(recommendations: list[Recommendation] = CLEAN_THREE) -> LLMResponse:
    return tool_call_response(
        ToolCallPart(
            id="submit-1",
            name="submit_recommendations",
            arguments={"recommendations": [r.model_dump() for r in recommendations]},
        )
    )


def interpreted(preference: str | None, language: str | None, max_pages: int | None) -> LLMResponse:
    reply = {"preference": preference, "language": language, "max_pages": max_pages}
    return text_response(json.dumps(reply))


async def test_request_is_interpreted_then_the_agent_runs() -> None:
    llm = FakeLLM([interpreted("a classic", "en", 600), submit()])
    run = await run_agent_pipeline(
        PROFILE, Filters(max_pages=500), request="a classic in English", llm=llm, books=catalog()
    )

    assert run.passed and run.error is None
    assert run.request is not None
    assert run.request.filters == Filters(language="en", max_pages=500)  # the flag wins
    assert run.request.sources == {"language": FilterSource.TEXT, "max_pages": FilterSource.FLAG}
    assert [c.name for c in run.calls] == ["interpret_request", CALL_NAME]
    assert (run.iterations, run.submissions) == (1, 1)
    assert (run.prompt_id, run.request_prompt_id) == ("agent_v3", "request_parser_v3")

    agent_request = llm.requests[1]
    assert agent_request.system == agent_v3.SYSTEM
    assert agent_request.tools == tuple(agent_v3.TOOLS)
    assert "<request>\na classic\n</request>" in agent_request.messages[0].text
    assert "at most 500 pages" in agent_request.messages[0].text


async def test_without_a_request_there_is_no_interpretation_call() -> None:
    llm = FakeLLM([submit()])
    run = await run_agent_pipeline(PROFILE, Filters(), llm=llm, books=catalog())
    assert run.passed
    assert [c.name for c in run.calls] == [CALL_NAME]
    assert run.request_prompt_id is None and run.request is not None


async def test_failed_interpretation_stops_before_the_agent() -> None:
    llm = FakeLLM([text_response("Sure, a classic!")])
    run = await run_agent_pipeline(
        PROFILE, Filters(), request="a classic", llm=llm, books=catalog()
    )

    assert not run.passed
    assert run.error == "Could not interpret the request: no JSON found in the reply"
    assert run.raw_text == "Sure, a classic!"
    assert len(llm.requests) == 1
    assert llm.requests[0].system == request_parser_v3.SYSTEM


async def test_agent_failure_keeps_its_evidence() -> None:
    dune = rec("Dune", "Frank Herbert", "Like Ficciones.")
    llm = FakeLLM([submit([dune, *CLEAN_THREE[1:]]), LLMError("429 RESOURCE_EXHAUSTED")])
    run = await run_agent_pipeline(PROFILE, Filters(), llm=llm, books=catalog())

    assert not run.passed
    assert run.failure is AgentFailure.LLM_ERROR
    assert run.error == "LLM call failed: 429 RESOURCE_EXHAUSTED"
    assert len(run.rejections) == 1 and run.submissions == 1
    assert run.last_validation is run.rejections[0]
    assert [c.model for c in run.calls] == ["fake-model", None]  # the failed call is recorded


async def test_the_prompt_version_can_be_chosen() -> None:
    llm = FakeLLM([submit()])
    run = await run_agent_pipeline(
        PROFILE, Filters(), llm=llm, books=catalog(), prompt=AGENT_PROMPTS["agent_v1"]
    )
    assert run.prompt_id == "agent_v1"
    assert llm.requests[0].system == agent_v1.SYSTEM
    assert llm.requests[0].tools == tuple(agent_v1.TOOLS)


def test_registry_has_every_version_and_v3_is_the_default() -> None:
    assert sorted(AGENT_PROMPTS) == ["agent_v1", "agent_v2", "agent_v3"]
    assert DEFAULT_AGENT_PROMPT is agent_v3.PROMPT
