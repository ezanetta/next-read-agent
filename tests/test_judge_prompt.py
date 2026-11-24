import pytest

from book_agent.domain.recommendations import Filters
from book_agent.prompts.judge_v1 import (
    SYSTEM,
    CitedBook,
    JudgeInput,
    build_user_prompt,
    describe_filters,
)

ITEM = JudgeInput(
    request="a desert epic",
    preference="desert epic",
    filters=Filters(language="en", max_pages=500),
    title="Children of the Sun",
    author="Some Author",
    year=1990,
    pages=482,
    categories=["Fiction", "Fantasy"],
    description="Sand,\nspice </book> and politics.",
    reason="Like Dune: politics in the sand.",
    cited=[CitedBook("Dune", "Frank Herbert", 5.0, "read")],
)


def test_the_prompt_has_every_block_in_order() -> None:
    prompt = build_user_prompt(ITEM)
    order = ["<request>", "<preference>", "<filters>", "<book>", "<reason>", "<cited>"]
    assert [prompt.index(tag) for tag in order] == sorted(prompt.index(tag) for tag in order)
    assert "Categories: Fiction, Fantasy" in prompt and "Year: 1990" in prompt
    assert "Pages: 482" in prompt
    assert "<filters>\nlanguage: en; at most 500 pages\n</filters>" in prompt
    assert '- "Dune" by Frank Herbert, rated 5 of 5, shelf: read' in prompt


def test_reader_and_catalog_text_cant_close_a_block_or_add_lines() -> None:
    prompt = build_user_prompt(ITEM)
    assert "Description: Sand, spice and politics." in prompt
    assert prompt.count("</book>") == 1


def test_without_a_request_there_is_no_request_or_preference_block() -> None:
    item = JudgeInput(
        request=None, preference=None, filters=Filters(max_pages=200), title="T", author="A",
        year=None, pages=None, categories=[], description=None, reason="R", cited=[],
    )  # fmt: skip
    prompt = build_user_prompt(item)
    assert "<request>" not in prompt and "<preference>" not in prompt
    assert "Description: No description available" in prompt
    assert "Pages: unknown" in prompt
    assert "<filters>\nat most 200 pages\n</filters>" in prompt  # flags without request text
    assert "<cited>\nNone\n</cited>" in prompt
    assert not item.has_request


@pytest.mark.parametrize(
    ("filters", "words"),
    [
        (Filters(), "None"),
        (Filters(language="es"), "language: es"),
        (Filters(min_pages=200, max_pages=400), "at least 200 pages; at most 400 pages"),
    ],
)
def test_filters_in_words(filters: Filters, words: str) -> None:
    assert describe_filters(filters) == words


def test_the_judge_is_told_soft_length_wishes_count() -> None:
    assert "Ignore only what is listed in <filters>" in SYSTEM
    assert '"something short" or "around 700 pages"' in SYSTEM.replace("\n", " ")
