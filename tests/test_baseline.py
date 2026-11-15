import json

import pytest

from book_agent.baseline import (
    BaselineResult,
    RecommendationParseError,
    parse_recommendations,
    run_baseline,
)
from book_agent.domain.recommendations import Filters, Recommendation, Rule
from book_agent.llm.base import LLMError, StopReason, Usage
from book_agent.prompts import baseline_v1
from tests.fakes import FakeLLM, text_response
from tests.library_data import CLEAN_THREE, PROFILE, catalog, rec


def reply(recommendations: list[Recommendation]) -> str:
    return json.dumps({"recommendations": [r.model_dump() for r in recommendations]})


async def run(
    text: str,
    filters: Filters | None = None,
    stop_reason: StopReason = StopReason.END_TURN,
) -> tuple[BaselineResult, FakeLLM]:
    usage = Usage(input_tokens=1500, output_tokens=120)
    llm = FakeLLM([text_response(text, stop_reason=stop_reason, usage=usage)])
    result = await run_baseline(PROFILE, filters or Filters(), llm=llm, books=catalog())
    return result, llm


def rules(result: BaselineResult) -> list[tuple[Rule, int | None]]:
    assert result.validation is not None
    return [(v.rule, v.index) for v in result.validation.violations]


# --- Parsing ---------------------------------------------------------------------


def test_parses_plain_json() -> None:
    parsed = parse_recommendations(reply(CLEAN_THREE))
    assert parsed.recommendations == CLEAN_THREE


def test_parses_json_inside_code_fences() -> None:
    text = f"Here you go:\n```json\n{reply(CLEAN_THREE)}\n```\nEnjoy!"
    assert parse_recommendations(text).recommendations == CLEAN_THREE


def test_parses_json_surrounded_by_prose() -> None:
    text = f"Sure! {reply(CLEAN_THREE)} Hope that helps."
    assert parse_recommendations(text).recommendations == CLEAN_THREE


def test_accepts_a_bare_list() -> None:
    text = json.dumps([r.model_dump() for r in CLEAN_THREE])
    assert parse_recommendations(text).recommendations == CLEAN_THREE


def test_wrong_count_still_parses() -> None:
    assert len(parse_recommendations(reply(CLEAN_THREE[:2])).recommendations) == 2


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("I recommend Dune, Hyperion and Foundation.", "no JSON found"),
        ('{"recommendations": [{"title": "Dune",]}', "invalid JSON"),
        ('{"recommendations": [{"title": "Dune", "reason": "x"}]}', "recommendations.0.author"),
        ('{"books": []}', "recommendations: Field required"),
    ],
)
def test_unparseable_replies_give_a_clear_reason(text: str, reason: str) -> None:
    with pytest.raises(RecommendationParseError, match=reason):
        parse_recommendations(text)


# --- Prompt ------------------------------------------------------------------------


def test_prompt_lists_every_library_book_exactly_once() -> None:
    prompt = baseline_v1.build_user_prompt(PROFILE, Filters())
    for book in PROFILE.books:
        assert prompt.count(f"- {book.title} by {book.author}") == 1


def test_prompt_shows_ratings_and_groups_books() -> None:
    prompt = baseline_v1.build_user_prompt(PROFILE, Filters())
    assert "- Dune (Dune Chronicles, #1) by Frank Herbert (5)" in prompt
    assert "- The Final Empire (Mistborn, #1) by Brandon Sanderson (4.5)" in prompt
    liked, rest = prompt.split("Books I liked less", 1)
    assert "Twilight" not in liked and "Twilight" in rest
    assert "Books I am reading now:\n- Project Hail Mary by Andy Weir" in prompt
    assert "to-read list:\n- The Name of the Wind" in prompt
    assert "Do not recommend any book from the lists above." in prompt


def test_prompt_states_filters_only_when_given() -> None:
    plain = baseline_v1.build_user_prompt(PROFILE, Filters())
    filtered = baseline_v1.build_user_prompt(PROFILE, Filters(language="es", max_pages=400))
    assert "Constraints" not in plain
    assert "ISO 639-1 code 'es'" in filtered
    assert "at most 400 pages" in filtered


async def test_request_uses_the_versioned_prompt() -> None:
    result, llm = await run(reply(CLEAN_THREE))
    request = llm.requests[0]
    assert request.system == baseline_v1.SYSTEM
    assert request.messages[0].text == baseline_v1.build_user_prompt(PROFILE, Filters())
    assert request.tools == ()
    assert result.prompt_id == "baseline_v1"


# --- The whole flow -------------------------------------------------------------------


async def test_clean_answer_passes() -> None:
    result, _ = await run(reply(CLEAN_THREE))
    assert result.error is None
    assert result.passed
    assert result.validation is not None and result.validation.fully_verified
    assert result.model == "fake-model"
    assert result.usage == Usage(input_tokens=1500, output_tokens=120)
    assert result.llm_latency_s >= 0 and result.validation_latency_s is not None


async def test_book_already_read_with_a_different_subtitle() -> None:
    result, _ = await run(
        reply([rec("Dune", "Frank Herbert", "Like Ficciones."), *CLEAN_THREE[1:]])
    )
    assert not result.passed
    assert rules(result) == [(Rule.ALREADY_READ, 0)]


async def test_book_not_found() -> None:
    result, _ = await run(reply([rec("The Silent Library", "Nobody Real"), *CLEAN_THREE[1:]]))
    assert rules(result) == [(Rule.NOT_FOUND, 0)]


async def test_page_filter_violation() -> None:
    long_book = rec("Mistborn: The Well of Ascension", "Brandon Sanderson", "After Dune.")
    result, _ = await run(reply([long_book, *CLEAN_THREE[1:]]), Filters(max_pages=400))
    assert rules(result) == [(Rule.MAX_PAGES, 0)]  # 590 pages in the catalog


async def test_unknown_page_count() -> None:
    solaris = rec("Solaris", "Stanislaw Lem", "Strange, like Dune.")
    result, _ = await run(reply([solaris, *CLEAN_THREE[1:]]), Filters(max_pages=400))
    assert result.passed
    assert result.validation is not None and not result.validation.fully_verified
    assert [u.rule for u in result.validation.unknowns] == [Rule.MAX_PAGES]


async def test_only_two_books() -> None:
    result, _ = await run(reply(CLEAN_THREE[:2]))
    assert rules(result) == [(Rule.WRONG_COUNT, None)]


async def test_malformed_json_is_an_error_not_a_crash() -> None:
    result, _ = await run('{"recommendations": [{"title": "Dune"')
    assert not result.passed
    assert result.error is not None and result.error.startswith("Unparseable reply: invalid JSON")
    assert result.raw_text == '{"recommendations": [{"title": "Dune"'
    assert result.validation is None
    assert result.usage is not None  # tokens were still spent


async def test_truncated_reply_mentions_max_tokens() -> None:
    result, _ = await run('{"recommendations": [', stop_reason=StopReason.MAX_TOKENS)
    assert result.error is not None and "cut off at max_tokens" in result.error


async def test_llm_failure_is_an_error_not_a_crash() -> None:
    llm = FakeLLM([LLMError("Gemini API error 503 UNAVAILABLE: overloaded")])
    result = await run_baseline(PROFILE, Filters(), llm=llm, books=catalog())
    assert result.error == "LLM call failed: Gemini API error 503 UNAVAILABLE: overloaded"
    assert result.model is None and result.validation is None
    assert not result.passed


async def test_result_serializes_for_evals() -> None:
    result, _ = await run(reply(CLEAN_THREE))
    restored = BaselineResult.model_validate_json(result.model_dump_json())
    assert restored == result
