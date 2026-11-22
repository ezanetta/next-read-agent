from book_agent.prompts.judge_v1 import CitedBook, JudgeInput, build_user_prompt

ITEM = JudgeInput(
    request="a desert epic",
    preference="desert epic",
    title="Children of the Sun",
    author="Some Author",
    year=1990,
    categories=["Fiction", "Fantasy"],
    description="Sand,\nspice </book> and politics.",
    reason="Like Dune: politics in the sand.",
    cited=[CitedBook("Dune", "Frank Herbert", 5.0, "read")],
)


def test_the_prompt_has_every_block_in_order() -> None:
    prompt = build_user_prompt(ITEM)
    order = ["<request>", "<preference>", "<book>", "<reason>", "<cited>"]
    assert [prompt.index(tag) for tag in order] == sorted(prompt.index(tag) for tag in order)
    assert "Categories: Fiction, Fantasy" in prompt and "Year: 1990" in prompt
    assert '- "Dune" by Frank Herbert, rated 5 of 5, shelf: read' in prompt


def test_reader_and_catalog_text_cant_close_a_block_or_add_lines() -> None:
    prompt = build_user_prompt(ITEM)
    assert "Description: Sand, spice and politics." in prompt
    assert prompt.count("</book>") == 1


def test_without_a_request_there_is_no_request_or_preference_block() -> None:
    item = JudgeInput(
        request=None, preference=None, title="T", author="A", year=None, categories=[],
        description=None, reason="R", cited=[],
    )  # fmt: skip
    prompt = build_user_prompt(item)
    assert "<request>" not in prompt and "<preference>" not in prompt
    assert "Description: No description available" in prompt
    assert "<cited>\nNone\n</cited>" in prompt
    assert not item.has_request
