from pathlib import Path

import pytest

from book_agent.domain.models import Profile
from book_agent.domain.recommendations import Filters
from book_agent.evals.judge import (
    DEFAULT_JUDGE_PROMPT,
    JUDGE_PROMPTS,
    BookRef,
    JudgeConfig,
    JudgmentStore,
    Verdict,
    judge_book,
    judge_input,
    judgeable_items,
    parse_judgment,
    plan_books,
    retried_errors,
    run_judge,
)
from book_agent.evals.pacing import QuotaExhausted
from book_agent.evals.records import RecordStore
from book_agent.json_reply import ReplyParseError
from book_agent.llm.base import LLMResponse, LLMServerError
from tests.fakes import FakeLLM, text_response
from tests.judge_data import AGENT, checked, stored, verdict_reply
from tests.library_data import PROFILE

JUDGE = JudgeConfig(prompt_id="judge_v1", model="judge-model")
V1 = JUDGE_PROMPTS["judge_v1"]  # these tests cover the text-JSON path
RECORD = stored(
    "desert", [checked("Sand One"), checked("Lost", matched=False), checked("Sand Two")]
)


def test_the_frozen_judge_is_the_default() -> None:
    assert DEFAULT_JUDGE_PROMPT is JUDGE_PROMPTS["judge_v3"]  # D-039


def test_the_merged_filters_and_a_missing_page_count_reach_the_input() -> None:
    record = stored(
        "flags", [checked("Short", pages=None)], request=None,
        filters=Filters(language="en", max_pages=200),
    )  # fmt: skip
    item = judge_input(record, 0, PROFILE)
    assert item.filters == Filters(language="en", max_pages=200)
    assert item.pages is None and item.request is None


def test_only_matched_books_are_judged() -> None:
    assert judgeable_items(RECORD) == [0, 2]
    assert judgeable_items(stored("x", [], error_kind="llm_error").model_copy(
        update={"final_validation": None})) == []  # fmt: skip


def test_the_input_comes_from_the_record_and_the_library() -> None:
    item = judge_input(RECORD, 2, PROFILE)
    assert (item.request, item.preference) == ("a desert epic", "a desert epic")
    assert (item.title, item.author, item.year, item.pages) == (
        "Sand Two",
        "Some Author",
        1990,
        320,
    )
    assert item.filters == Filters()
    assert item.reason == "Like Dune: Sand Two"
    [cited] = item.cited
    assert (cited.author, cited.rating, cited.shelf) == ("Frank Herbert", 5.0, "read")
    with pytest.raises(ValueError, match="no catalog match"):
        judge_input(RECORD, 1, PROFILE)


@pytest.mark.parametrize(
    ("reply", "has_request", "problem"),
    [
        ('{"reason_quality": {"justification": "x", "verdict": "yes"}}', True, "missing"),
        (verdict_reply("yes").message.text, False, "without a request"),
        ('{"reason_quality": {"justification": "x", "verdict": "maybe"}}', False, "verdict"),
        ("Looks good!", False, "no JSON"),
    ],
)
def test_unusable_replies_are_rejected(reply: str, has_request: bool, problem: str) -> None:
    with pytest.raises(ReplyParseError, match=problem):
        parse_judgment(reply, has_request)


async def test_a_judgment_records_both_criteria_and_the_call() -> None:
    llm = FakeLLM([verdict_reply("partial", "no")])
    judgment = await judge_book(judge_input(RECORD, 0, PROFILE), 0, llm, V1)
    assert judgment.judged and judgment.had_description and judgment.has_request
    assert judgment.fits_request is not None and judgment.fits_request.verdict is Verdict.PARTIAL
    assert judgment.reason_quality is not None and judgment.reason_quality.verdict is Verdict.NO
    assert judgment.call is not None and judgment.call.name == "judge"
    assert llm.requests[0].temperature == 0.0


async def test_without_a_request_only_reason_quality_is_judged() -> None:
    record = stored("lib", [checked("Sand One", description=None)], request=None)
    judgment = await judge_book(
        judge_input(record, 0, PROFILE), 0, FakeLLM([verdict_reply(None)]), V1
    )
    assert judgment.fits_request is None and judgment.reason_quality is not None
    assert not judgment.had_description and not judgment.has_request


@pytest.mark.parametrize(
    ("script", "error"),
    [
        ([LLMServerError("503", status=503, retries=1)], "LLM call failed: 503"),
        ([text_response("Sure!")], "unreadable reply: no JSON"),
    ],
)
async def test_failures_become_unjudged_books(
    script: list[LLMResponse | Exception], error: str
) -> None:
    judgment = await judge_book(judge_input(RECORD, 0, PROFILE), 0, FakeLLM(script), V1)
    assert not judgment.judged and judgment.error is not None and judgment.error.startswith(error)


def profiles(_: str) -> Profile:
    return PROFILE


async def judge_all(
    tmp_path: Path, script: list[LLMResponse | Exception]
) -> tuple[JudgmentStore, int, str | None]:
    store = JudgmentStore(RecordStore(tmp_path))
    plan = plan_books([RECORD], store, "set_v1", JUDGE)
    summary = await run_judge(
        plan, case_set="set_v1", judge=JUDGE, profiles=profiles, llm=FakeLLM(script),
        store=store, git_commit="abc",
    )  # fmt: skip
    return store, summary.judged, summary.stopped


async def test_judgments_are_saved_per_record_and_resume(tmp_path: Path) -> None:
    store, judged, _ = await judge_all(tmp_path, [verdict_reply(), text_response("oops")])
    assert judged == 2
    [file] = store.load_all("set_v1", JUDGE)
    assert (file.config_id, file.case_id, file.judge) == (AGENT.id, "desert", JUDGE)
    assert [(j.item_index, j.judged) for j in file.judgments] == [(0, True), (2, False)]
    # A rerun retries only the failed book, and keeps the other one.
    plan = plan_books([RECORD], store, "set_v1", JUDGE)
    assert [p.item_index for p in plan] == [2]
    await run_judge(
        plan, case_set="set_v1", judge=JUDGE, profiles=profiles,
        llm=FakeLLM([verdict_reply()]), store=store, git_commit="abc",
    )  # fmt: skip
    judged_now = store.judged("set_v1", JUDGE.id)
    assert all(j.judged for j in judged_now.values()) and len(judged_now) == 2


async def test_a_quota_stop_keeps_finished_judgments(tmp_path: Path) -> None:
    store, judged, stopped = await judge_all(
        tmp_path, [verdict_reply(), QuotaExhausted("daily quota exhausted")]
    )
    assert judged == 1 and stopped == "daily quota exhausted"
    assert list(store.judged("set_v1", JUDGE)) == [
        BookRef(config_id=AGENT.id, case_id="desert", run_index=1, item_index=0)
    ]


def test_the_plan_can_be_limited_to_some_books(tmp_path: Path) -> None:
    only = {BookRef(config_id=AGENT.id, case_id="desert", run_index=1, item_index=2)}
    plan = plan_books([RECORD], JudgmentStore(RecordStore(tmp_path)), "set_v1", JUDGE, only=only)
    assert [p.ref for p in plan] == list(only)


async def test_an_unknown_judge_prompt_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown judge prompt"):
        await run_judge(
            [], case_set="set_v1", judge=JUDGE.model_copy(update={"prompt_id": "judge_v9"}),
            profiles=profiles, llm=FakeLLM([]), store=JudgmentStore(RecordStore(tmp_path)),
            git_commit=None,
        )  # fmt: skip


async def test_retrying_an_error_backs_up_the_file_first(tmp_path: Path) -> None:
    store, _, _ = await judge_all(tmp_path, [verdict_reply(), text_response("oops")])
    plan = plan_books([RECORD], store, "set_v1", JUDGE)
    assert retried_errors(plan, store, "set_v1", JUDGE) == 1
    backup = tmp_path / "_backups" / "set_v1" / "stamp"
    await run_judge(
        plan, case_set="set_v1", judge=JUDGE, profiles=profiles,
        llm=FakeLLM([verdict_reply()]), store=store, git_commit="abc", backup=backup,
    )  # fmt: skip
    live = store.path("set_v1", JUDGE, AGENT.id, "desert", 1)
    old = backup / live.relative_to(tmp_path)
    assert "unreadable reply" in old.read_text() and "unreadable reply" not in live.read_text()
    assert len(store.load_all("set_v1", JUDGE)) == 1  # the backup is outside the judge folder
