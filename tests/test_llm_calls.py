from time import perf_counter

from book_agent.llm.base import LLMError, LLMServerError
from book_agent.llm.calls import LLMCall
from tests.fakes import text_response


def test_a_succeeded_call_carries_the_response_retries() -> None:
    response = text_response("ok").model_copy(update={"retries": 1})
    call = LLMCall.succeeded("agent", response, perf_counter())
    assert (call.model, call.retries, call.error_status) == ("fake-model", 1, None)
    assert call.usage == response.usage and call.latency_s >= 0


def test_a_failed_server_error_call_keeps_status_and_retries() -> None:
    call = LLMCall.failed("agent", LLMServerError("503", status=503, retries=1), perf_counter())
    assert (call.model, call.usage, call.retries, call.error_status) == (None, None, 1, 503)
    assert call.server_error


def test_other_failures_have_no_status() -> None:
    call = LLMCall.failed("agent", LLMError("bad request"), perf_counter())
    assert (call.retries, call.error_status, call.server_error) == (0, None, False)


def test_old_records_without_the_new_fields_still_load() -> None:
    call = LLMCall.model_validate({"name": "agent", "model": None, "usage": None, "latency_s": 1})
    assert (call.retries, call.error_status) == (0, None)
