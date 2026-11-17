"""Acceptance policy: which validation results the agent may return to the reader.

The validator reports honestly (violations and unknowns); this module decides what the
product does with them (D-014). Pure functions, so the policy is easy to test and change.
"""

from book_agent.domain.recommendations import Rule, Unknown, ValidationResult, Violation

BLOCKING_UNKNOWNS = frozenset({Rule.SAME_WORK})
"""Unknowns that reject a submission. SAME_WORK: the same-work check failed for an author
already in the library, so the book may be one the reader has read (D-012)."""

Problem = Violation | Unknown


def blocking_problems(validation: ValidationResult) -> list[Problem]:
    """Every violation, plus the unknowns that block acceptance, in report order."""
    blocking = [u for u in validation.unknowns if u.rule in BLOCKING_UNKNOWNS]
    return [*validation.violations, *blocking]


def is_acceptable(validation: ValidationResult) -> bool:
    """No violations and no blocking unknowns. Other unknowns (a missing page count or
    language) are accepted: the data is missing, not wrong."""
    return not blocking_problems(validation)


def rejected_indexes(validation: ValidationResult) -> list[int]:
    """Positions of the books with a blocking problem, in order, without repeats."""
    indexes = {p.index for p in blocking_problems(validation) if p.index is not None}
    return sorted(indexes)
