import dataclasses

from book_agent.evals.judge import JUDGE_PROMPTS
from book_agent.prompts import judge_v1, judge_v2
from tests.test_judge_prompt import ITEM

NO_REQUEST = dataclasses.replace(ITEM, request=None, preference=None)


def flat(text: str) -> str:
    return " ".join(text.split())


def test_v2_is_registered_next_to_a_frozen_v1() -> None:
    assert JUDGE_PROMPTS["judge_v2"].system == judge_v2.SYSTEM
    assert JUDGE_PROMPTS["judge_v1"].system == judge_v1.SYSTEM
    assert judge_v2.SYSTEM != judge_v1.SYSTEM


def test_with_a_request_the_user_prompt_is_v1s() -> None:
    assert judge_v2.build_user_prompt(ITEM) == judge_v1.build_user_prompt(ITEM)
    assert judge_v2.NO_REQUEST_REMINDER not in judge_v2.build_user_prompt(ITEM)


def test_without_a_request_the_prompt_ends_with_the_reminder() -> None:
    # F-015: v1 graded the filters as a request when there was no request text.
    prompt = judge_v2.build_user_prompt(NO_REQUEST)
    assert prompt == f"{judge_v1.build_user_prompt(NO_REQUEST)}\n{judge_v2.NO_REQUEST_REMINDER}"
    assert prompt.endswith("Answer reason_quality only.")
    assert "<filters>\nlanguage: en; at most 500 pages\n</filters>" in prompt


def test_reason_quality_judges_only_the_connection() -> None:
    system = flat(judge_v2.SYSTEM)
    assert "Judge only that connection, never whether the book fits the request." in system
    assert "Don't judge the reason here." in system
    assert "Judge each criterion on its own" in system


def test_a_mood_tone_or_genre_link_is_at_most_partial() -> None:
    system = flat(judge_v2.SYSTEM)
    assert "themes, structure, style, characters or setting" in system
    assert "the connection rests only on mood, tone or genre" in system


def test_a_book_of_another_form_than_the_request_names_is_a_no() -> None:
    system = flat(judge_v2.SYSTEM)
    assert (
        "When the request names a form (novel, short stories, non-fiction, poetry) and the "
        "book is another form, the verdict is no" in system
    )


def test_v1s_hard_filter_and_soft_length_rules_carry_over() -> None:
    system = flat(judge_v2.SYSTEM)
    assert "Ignore only what is listed in <filters>" in system
    assert '"something short" or "a doorstopper"' in system  # no calibration request text
