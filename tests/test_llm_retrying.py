import pytest
from pydantic import SecretStr

from book_agent.config import Settings
from book_agent.evals.pacing import QuotaExhausted
from book_agent.llm.base import LLMError, LLMRateLimitError, LLMResponse, LLMServerError, Message
from book_agent.llm.gemini import GeminiClient
from book_agent.llm.retrying import RETRY_DELAY_S, RetryingLLM
from book_agent.wiring import build_llm, build_provider
from tests.fakes import FakeLLM, text_response

UNAVAILABLE = LLMServerError("Gemini API error 503 UNAVAILABLE: high demand", status=503)


class Sleeps:
    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


def retrying(script: list[LLMResponse | Exception]) -> tuple[RetryingLLM, FakeLLM, Sleeps]:
    inner = FakeLLM(script)
    sleeps = Sleeps()
    return RetryingLLM(inner, jitter=lambda: 0.3, sleep=sleeps), inner, sleeps


async def ask(llm: RetryingLLM) -> LLMResponse:
    return await llm.complete(system="s", messages=[Message.user("hi")], max_tokens=50)


async def test_a_success_is_passed_through_without_waiting() -> None:
    llm, inner, sleeps = retrying([text_response("ok")])
    response = await llm.complete(system="s", messages=[Message.user("hi")], max_tokens=50)
    assert response.message.text == "ok" and response.retries == 0
    assert len(inner.requests) == 1 and sleeps.waits == []


async def test_a_server_error_is_retried_once_after_a_pause_with_jitter() -> None:
    llm, inner, sleeps = retrying([UNAVAILABLE, text_response("ok")])
    response = await llm.complete(
        system="s", messages=[Message.user("hi")], max_tokens=50, temperature=0.0
    )
    assert response.message.text == "ok"
    assert response.retries == 1
    assert sleeps.waits == [RETRY_DELAY_S + 0.3]
    assert len(inner.requests) == 2 and inner.requests[0] == inner.requests[1]


async def test_a_second_server_error_is_raised_and_marked_as_retried() -> None:
    llm, inner, _ = retrying([UNAVAILABLE, LLMServerError("Gemini API error 500", status=500)])
    with pytest.raises(LLMServerError) as error:
        await ask(llm)
    assert error.value.status == 500 and error.value.retries == 1
    assert str(error.value) == "Gemini API error 500 (after 1 retry)"
    assert len(inner.requests) == 2


@pytest.mark.parametrize(
    "failure",
    [
        LLMRateLimitError("429", retry_after=5),
        LLMError("Gemini API error 400 INVALID_ARGUMENT"),
        QuotaExhausted("daily quota exhausted"),
    ],
)
async def test_other_failures_are_not_retried(failure: Exception) -> None:
    llm, inner, sleeps = retrying([failure])
    with pytest.raises(type(failure)):
        await ask(llm)
    assert len(inner.requests) == 1 and sleeps.waits == []


def test_wiring_wraps_the_bare_provider_for_every_caller() -> None:
    settings = Settings(gemini_api_key=SecretStr("test-key"))
    assert isinstance(build_provider(settings), GeminiClient)
    assert isinstance(build_llm(settings), RetryingLLM)
