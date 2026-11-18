import pytest

from book_agent.evals.pacing import DEFAULT_RETRY_WAIT_S, PacedLLM, QuotaExhausted
from book_agent.llm.base import LLMClient, LLMError, LLMRateLimitError, LLMResponse, Message
from tests.fakes import FakeLLM, text_response


class FakeClock:
    """A clock that only moves when the code under test sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def paced(
    script: list[LLMResponse | Exception], clock: FakeClock, max_rpm: int = 15
) -> tuple[PacedLLM, FakeLLM]:
    inner = FakeLLM(script)
    return PacedLLM(inner, max_rpm=max_rpm, clock=clock, sleep=clock.sleep), inner


async def ask(llm: PacedLLM) -> str:
    response = await llm.complete(system="s", messages=[Message.user("hi")], max_tokens=10)
    return response.message.text


def test_implements_the_port() -> None:
    clock = FakeClock()
    llm: LLMClient = paced([], clock)[0]
    assert llm is not None


async def test_requests_are_spaced_to_the_rpm_limit() -> None:
    clock = FakeClock()
    llm, _ = paced([text_response("a"), text_response("b"), text_response("c")], clock)
    await ask(llm)
    clock.now += 1.0  # 1 s of work between requests
    await ask(llm)
    await ask(llm)
    assert clock.sleeps == [3.0, 4.0]  # 60 / 15 = 4 s between starts
    assert llm.requests == 3


async def test_no_wait_when_requests_are_already_far_apart() -> None:
    clock = FakeClock()
    llm, _ = paced([text_response("a"), text_response("b")], clock)
    await ask(llm)
    clock.now += 10.0
    await ask(llm)
    assert clock.sleeps == []


async def test_a_rate_limit_waits_retry_after_once_and_retries() -> None:
    clock = FakeClock()
    llm, inner = paced([LLMRateLimitError("429", retry_after=21.0), text_response("ok")], clock)
    assert await ask(llm) == "ok"
    assert clock.sleeps[0] == 21.0
    assert len(inner.requests) == 2 and llm.requests == 2


async def test_without_retry_after_the_default_wait_is_used() -> None:
    clock = FakeClock()
    llm, _ = paced([LLMRateLimitError("429"), text_response("ok")], clock)
    await ask(llm)
    assert clock.sleeps[0] == DEFAULT_RETRY_WAIT_S


async def test_two_rate_limits_in_a_row_stop_the_run() -> None:
    clock = FakeClock()
    llm, inner = paced(
        [LLMRateLimitError("429 a", retry_after=5.0), LLMRateLimitError("429 b", retry_after=5.0)],
        clock,
    )
    with pytest.raises(QuotaExhausted, match="rate limited twice in a row: 429 b"):
        await ask(llm)
    assert len(inner.requests) == 2


async def test_a_daily_quota_stops_at_once_without_waiting() -> None:
    clock = FakeClock()
    llm, inner = paced([LLMRateLimitError("429 per day", retry_after=30.0, daily=True)], clock)
    with pytest.raises(QuotaExhausted, match="daily quota exhausted"):
        await ask(llm)
    assert clock.sleeps == [] and len(inner.requests) == 1


async def test_quota_exhausted_is_not_an_llm_error() -> None:
    # Code that turns LLMErrors into recorded failures must not swallow the stop.
    assert not issubclass(QuotaExhausted, LLMError)


async def test_other_errors_pass_through_untouched() -> None:
    clock = FakeClock()
    llm, _ = paced([LLMError("500 internal")], clock)
    with pytest.raises(LLMError, match="500 internal"):
        await ask(llm)


def test_max_rpm_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_rpm"):
        PacedLLM(FakeLLM([]), max_rpm=0)
