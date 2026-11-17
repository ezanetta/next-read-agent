from book_agent.domain.recommendations import Rule, ValidationResult, Violation
from book_agent.prompts.submission_feedback_v1 import build_feedback
from tests.library_data import CLEAN_THREE


def feedback(*violations: Violation) -> str:
    validation = ValidationResult(items=[], violations=list(violations), unknowns=[])
    return build_feedback(CLEAN_THREE, validation)


def test_several_rejected_books_are_listed_and_named_in_plural() -> None:
    text = feedback(
        Violation(rule=Rule.MAX_PAGES, index=0, message="'Hyperion' by Dan Simmons: 482 pages"),
        Violation(rule=Rule.CITES_LIBRARY, index=2, message="'Foundation' by Isaac Asimov: x"),
    )
    assert text.splitlines() == [
        "Submission rejected. Problems found:",
        "- Book 1: 'Hyperion' by Dan Simmons: 482 pages",
        "- Book 3: 'Foundation' by Isaac Asimov: x",
        'Accepted: 2 "The Left Hand of Darkness". Keep them unchanged.',
        "Replace only books 1, 3 with new books that meet every rule.",
        "Then call submit_recommendations again with exactly 3 books.",
    ]


def test_list_level_problem_without_rejected_books() -> None:
    text = feedback(Violation(rule=Rule.WRONG_COUNT, index=None, message="expected 3, got 4"))
    assert "- The list: expected 3, got 4" in text
    assert "Replace only" not in text
    assert text.endswith("Then call submit_recommendations again with exactly 3 books.")


def test_no_accepted_line_when_every_book_is_rejected() -> None:
    text = feedback(
        *(Violation(rule=Rule.ALREADY_READ, index=i, message=f"book {i}") for i in range(3))
    )
    assert "Accepted:" not in text
    assert "Replace only books 1, 2, 3" in text
