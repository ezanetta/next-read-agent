from collections.abc import Sequence

import pytest

from book_agent.domain.acceptance import (
    blocking_problems,
    infrastructure_failures,
    is_acceptable,
    rejected_indexes,
)
from book_agent.domain.recommendations import Rule, Unknown, ValidationResult, Violation


def result(
    violations: Sequence[Violation] = (), unknowns: Sequence[Unknown] = ()
) -> ValidationResult:
    return ValidationResult(items=[], violations=list(violations), unknowns=list(unknowns))


def violation(rule: Rule, index: int | None) -> Violation:
    return Violation(rule=rule, index=index, message="x")


def unknown(rule: Rule, index: int) -> Unknown:
    return Unknown(rule=rule, index=index, message="x")


@pytest.mark.parametrize(
    ("validation", "acceptable"),
    [
        (result(), True),
        (result(unknowns=[unknown(Rule.MAX_PAGES, 0), unknown(Rule.LANGUAGE, 1)]), True),
        (result(unknowns=[unknown(Rule.NOT_FOUND, 2)]), False),  # existence unverified
        (result(unknowns=[unknown(Rule.SAME_WORK, 0)]), False),  # D-012
        (result(violations=[violation(Rule.MAX_PAGES, 1)]), False),
        (result(violations=[violation(Rule.WRONG_COUNT, None)]), False),
    ],
)
def test_is_acceptable(validation: ValidationResult, acceptable: bool) -> None:
    assert is_acceptable(validation) is acceptable


def test_blocking_problems_are_violations_then_blocking_unknowns() -> None:
    same_work = unknown(Rule.SAME_WORK, 2)
    already_read = violation(Rule.ALREADY_READ, 0)
    validation = result(violations=[already_read], unknowns=[unknown(Rule.MAX_PAGES, 1), same_work])
    assert blocking_problems(validation) == [already_read, same_work]


def test_rejected_indexes_are_sorted_unique_and_skip_list_level_problems() -> None:
    validation = result(
        violations=[
            violation(Rule.WRONG_COUNT, None),
            violation(Rule.CITES_LIBRARY, 2),
            violation(Rule.MAX_PAGES, 2),
        ],
        unknowns=[unknown(Rule.SAME_WORK, 0), unknown(Rule.MAX_PAGES, 1)],
    )
    assert rejected_indexes(validation) == [0, 2]


def test_infrastructure_failures_are_only_failed_lookups() -> None:
    lookup = unknown(Rule.NOT_FOUND, 1)
    validation = result(
        violations=[violation(Rule.NOT_FOUND, 0)],  # a book that is really missing
        unknowns=[unknown(Rule.SAME_WORK, 2), lookup, unknown(Rule.MAX_PAGES, 0)],
    )
    assert infrastructure_failures(validation) == [lookup]
