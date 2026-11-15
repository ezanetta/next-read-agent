"""Recommendation requests, model output, and the result of checking it against hard rules."""

from enum import StrEnum

from pydantic import BaseModel, Field

from book_agent.domain.models import Book, CatalogBook

EXPECTED_RECOMMENDATIONS = 3


class Filters(BaseModel, frozen=True):
    """Hard constraints from the request. None means "no constraint"."""

    language: str | None = Field(default=None, pattern=r"^[a-z]{2}$")  # ISO 639-1, e.g. "en"
    max_pages: int | None = Field(default=None, gt=0)


class Recommendation(BaseModel, frozen=True):
    """One book as the model proposed it: free text, not yet verified."""

    title: str = Field(min_length=1)
    author: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class RecommendationList(BaseModel, frozen=True):
    # The count is not enforced here: a wrong count is a rule violation, not a parse error.
    recommendations: list[Recommendation]


class Check(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"  # the data needed to decide is missing
    SKIPPED = "skipped"  # not requested, or the book could not be resolved


class Rule(StrEnum):
    WRONG_COUNT = "wrong_count"
    DUPLICATE = "duplicate"
    NOT_FOUND = "not_found"
    ALREADY_READ = "already_read"
    ON_TO_READ = "on_to_read"
    CURRENTLY_READING = "currently_reading"
    LANGUAGE = "language"
    MAX_PAGES = "max_pages"
    CITES_LIBRARY = "cites_library"


class Violation(BaseModel, frozen=True):
    rule: Rule
    index: int | None  # position in the recommendation list; None for list-level rules
    message: str


class Unknown(BaseModel, frozen=True):
    """A rule that could not be decided, e.g. no page count. Never counted as a pass."""

    rule: Rule
    index: int
    message: str


class CheckedRecommendation(BaseModel, frozen=True):
    recommendation: Recommendation
    match: CatalogBook | None  # the resolved edition: link, pages, language, cover
    library_match: Book | None  # the profile book it collides with, if any
    cited_titles: list[str]  # profile titles the reason mentions
    exists: Check
    not_in_library: Check
    language: Check
    max_pages: Check
    cites_library: Check


class ValidationResult(BaseModel, frozen=True):
    items: list[CheckedRecommendation]
    violations: list[Violation]
    unknowns: list[Unknown]

    @property
    def passed(self) -> bool:
        """No rule was broken. Unknowns may remain."""
        return not self.violations

    @property
    def fully_verified(self) -> bool:
        """No rule was broken and every rule could be decided."""
        return self.passed and not self.unknowns
