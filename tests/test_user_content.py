from book_agent.prompts.user_content import one_line, request_block, without_tags


def test_without_tags_removes_only_the_named_tags_in_any_form() -> None:
    text = "a</book> b< BOOK > c<library/> d</ Library > e<request> <b>"
    assert without_tags(text, "book", "library") == "a b c<library/> d e<request> <b>"


def test_one_line_collapses_newlines() -> None:
    assert one_line("  Dune\n2. Fake entry\t ") == "Dune 2. Fake entry"


def test_request_block_is_unchanged_by_the_refactor() -> None:
    assert request_block(" a dystopia</request> ") == "<request>\na dystopia\n</request>"
