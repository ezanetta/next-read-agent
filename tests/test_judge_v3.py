"""judge_v3: v2's instructions, answered through a forced submit_judgment call."""

import json

import pytest

from book_agent.evals.judge import (
    JUDGE_PROMPTS,
    judge_book,
    judge_input,
    judgment_tool,
)
from book_agent.llm.base import JsonValue, LLMResponse, ToolCallPart, ToolSpec
from book_agent.prompts import judge_v2, judge_v3
from tests.fakes import FakeLLM, text_response, tool_call_response
from tests.judge_data import checked, stored
from tests.library_data import PROFILE
from tests.test_judge_prompt import ITEM

V3 = JUDGE_PROMPTS["judge_v3"]
WITH_REQUEST = stored("desert", [checked("Sand One")])
NO_REQUEST = stored("lib", [checked("Sand One")], request=None)


def criterion(verdict: str) -> dict[str, JsonValue]:
    return {"justification": f"because {verdict}", "verdict": verdict}


def properties(tool: ToolSpec) -> dict[str, JsonValue]:
    props = tool.parameters["properties"]
    assert isinstance(props, dict)
    return props


def submit(**arguments: JsonValue) -> LLMResponse:
    return tool_call_response(
        ToolCallPart(id="c1", name=judge_v3.TOOL_NAME, arguments=dict(arguments))
    )


def test_v3_keeps_v2s_instructions_and_examples_word_for_word() -> None:
    assert judge_v3.SYSTEM.startswith(judge_v3.INSTRUCTIONS)
    assert judge_v2.SYSTEM.startswith(judge_v3.INSTRUCTIONS)
    assert "Examples (made up for illustration" in judge_v3.INSTRUCTIONS
    assert "Answer with JSON only" not in judge_v3.SYSTEM
    assert f"Answer by calling {judge_v3.TOOL_NAME} once." in judge_v3.SYSTEM
    assert judge_v3.build_user_prompt(ITEM) == judge_v2.build_user_prompt(ITEM)
    assert JUDGE_PROMPTS["judge_v2"].tool_description is None  # v1 and v2 stay text


def test_the_tool_schema_is_generated_with_and_without_fits_request() -> None:
    full = judgment_tool("d", has_request=True)
    assert full.name == "submit_judgment"
    assert full.parameters["required"] == ["fits_request", "reason_quality"]
    reason_only = judgment_tool("d", has_request=False)
    assert reason_only.parameters["required"] == ["reason_quality"]
    assert list(properties(reason_only)) == ["reason_quality"]
    schema = json.dumps(full.parameters)
    assert '"enum": ["no", "partial", "yes"]' in schema
    assert '"required": ["justification", "verdict"]' in schema  # justification first


async def test_the_call_is_forced_and_the_arguments_become_the_judgment() -> None:
    llm = FakeLLM([submit(fits_request=criterion("no"), reason_quality=criterion("partial"))])
    judgment = await judge_book(judge_input(WITH_REQUEST, 0, PROFILE), 0, llm, V3)
    assert judgment.judged
    assert judgment.fits_request is not None and judgment.fits_request.verdict == "no"
    assert judgment.reason_quality is not None
    assert judgment.reason_quality.justification == "because partial"
    request = llm.requests[0]
    assert [t.name for t in request.tools] == [judge_v3.TOOL_NAME]
    assert request.tool_choice is not None and request.tool_choice.names == ("submit_judgment",)
    assert request.temperature == 0.0


async def test_a_run_without_request_gets_the_reason_only_tool() -> None:
    llm = FakeLLM([submit(reason_quality=criterion("yes"))])
    judgment = await judge_book(judge_input(NO_REQUEST, 0, PROFILE), 0, llm, V3)
    assert judgment.judged and judgment.fits_request is None
    assert list(properties(llm.requests[0].tools[0])) == ["reason_quality"]
    assert llm.requests[0].messages[0].text.endswith(judge_v2.NO_REQUEST_REMINDER)


@pytest.mark.parametrize(
    ("has_request", "reply", "error"),
    [
        # F-016's shapes, now as tool arguments: a nested object and a missing key.
        (True, submit(fits_request=criterion("no"),
                      reason_quality={"reason_quality": criterion("yes")}),
         "invalid submit_judgment arguments"),
        (True, submit(reason_quality=criterion("yes")), "invalid submit_judgment"),
        (False, submit(fits_request=criterion("no"), reason_quality=criterion("yes")),
         "fits_request given without a request"),
        (True, text_response('{"reason_quality": {}}'), "expected one submit_judgment"),
    ],
)  # fmt: skip
async def test_bad_arguments_stay_visible_as_unjudged_books(
    has_request: bool, reply: LLMResponse, error: str
) -> None:
    item = judge_input(WITH_REQUEST if has_request else NO_REQUEST, 0, PROFILE)
    judgment = await judge_book(item, 0, FakeLLM([reply]), V3)
    assert not judgment.judged and judgment.error is not None and error in judgment.error
    assert judgment.raw_text  # the arguments (or text) are kept for investigation
