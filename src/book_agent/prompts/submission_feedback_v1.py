"""submission_feedback_v1: what the agent tells the model when a submission is rejected.

Part of what the model reads, so it is versioned like a prompt: changes go in
submission_feedback_v2 once evals use it.
"""

from book_agent.domain.acceptance import Problem, blocking_problems, rejected_indexes
from book_agent.domain.recommendations import (
    Recommendation,
    Rule,
    Unknown,
    ValidationResult,
)

PROMPT_ID = "submission_feedback_v1"


def build_feedback(recommendations: list[Recommendation], validation: ValidationResult) -> str:
    """The rejection message: each problem with its book number, then which books to keep
    and which to replace.

    Violation messages already name the book ("'Dune' by Frank Herbert: already read"),
    so each line adds only the number the model can refer to.
    """
    lines = ["Submission rejected. Problems found:"]
    lines += [f"- {_describe(problem)}" for problem in blocking_problems(validation)]

    rejected = rejected_indexes(validation)
    kept = [i for i in range(len(recommendations)) if i not in rejected]
    if kept:
        books = ", ".join(_book(i, recommendations[i]) for i in kept)
        lines.append(f"Accepted: {books}. Keep them unchanged.")
    if rejected:
        numbers = ", ".join(str(i + 1) for i in rejected)
        lines.append(
            f"Replace only book{'s' if len(rejected) > 1 else ''} {numbers} with new books "
            "that meet every rule."
        )
    lines.append("Then call submit_recommendations again with exactly 3 books.")
    return "\n".join(lines)


def _describe(problem: Problem) -> str:
    if problem.index is None:
        return f"The list: {problem.message}"
    text = f"Book {problem.index + 1}: {problem.message}"
    if isinstance(problem, Unknown) and problem.rule is Rule.SAME_WORK:
        text += ". It could not be verified as new to the reader, so replace it"
    return text


def _book(index: int, recommendation: Recommendation) -> str:
    return f'{index + 1} "{recommendation.title}"'
