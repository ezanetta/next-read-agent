"""baseline_v2 and agent_v3 state the minimum page count; nothing else changes (D-026)."""

from book_agent.domain.recommendations import Filters
from book_agent.prompts import agent_v2, agent_v3, baseline_v1, baseline_v2
from tests.library_data import PROFILE

ALL = Filters(language="en", max_pages=500, min_pages=300)


def test_baseline_v2_states_the_minimum() -> None:
    prompt = baseline_v2.build_user_prompt(PROFILE, ALL)
    assert "- Every book must have at least 300 pages." in prompt
    assert "- Every book must have at most 500 pages." in prompt


def test_baseline_v2_is_byte_identical_to_v1_without_a_minimum() -> None:
    for filters in (Filters(), Filters(language="es", max_pages=400)):
        for preference in (None, "algo como Dune"):
            assert baseline_v2.build_user_prompt(PROFILE, filters, preference) == (
                baseline_v1.build_user_prompt(PROFILE, filters, preference)
            )
    assert baseline_v2.SYSTEM == baseline_v1.SYSTEM


def test_baseline_v1_stays_frozen_and_ignores_the_minimum() -> None:
    assert "at least" not in baseline_v1.build_user_prompt(PROFILE, ALL)


def test_agent_v3_changes_only_the_user_prompt() -> None:
    assert agent_v3.SYSTEM == agent_v2.SYSTEM
    assert agent_v3.TOOLS == agent_v2.TOOLS
    assert "at least 300 pages" in agent_v3.build_user_prompt(PROFILE, ALL, None)
    assert "at least" not in agent_v2.build_user_prompt(PROFILE, ALL, None)
