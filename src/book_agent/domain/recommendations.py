"""Recommendation requests, model output, and the result of checking it against hard rules."""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, model_validator

from book_agent.domain.models import Book, CatalogBook

EXPECTED_RECOMMENDATIONS = 3


class Filters(BaseModel, frozen=True):
    """Hard constraints from the request. None means "no constraint".

    Page bounds are inclusive (D-006, D-026): "under 400" is max_pages 400, "more than
    600" is min_pages 600.
    """

    language: str | None = Field(default=None, pattern=r"^[a-z]{2}$")  # ISO 639-1, e.g. "en"
    max_pages: int | None = Field(default=None, gt=0)
    min_pages: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _bounds_in_order(self) -> Self:
        if (
            self.min_pages is not None
            and self.max_pages is not None
            and self.min_pages > self.max_pages
        ):
            raise ValueError(f"min_pages {self.min_pages} is above max_pages {self.max_pages}")
        return self


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
    MIN_PAGES = "min_pages"
    CITES_LIBRARY = "cites_library"
    # Only as an Unknown: the same-work check failed for an author already in the library.
    # Not a pass: the book may be a translation or retitled edition of one already there.
    SAME_WORK = "same_work"


class MatchEvidence(StrEnum):
    """What decided that a recommendation is a book already in the library."""

    ISBN = "isbn"
    TITLE = "title"  # title variants and author surname, as in matching.same_work
    WORK_KEY = "work_key"  # an edition of the recommendation shares a work key with the book
    LLM_SAME = "llm_same"  # the LLM same-work judge answered SAME
    LLM_UNSURE = "llm_unsure"  # the judge was unsure, which counts as same

    @property
    def by_llm(self) -> bool:
        return self in (MatchEvidence.LLM_SAME, MatchEvidence.LLM_UNSURE)


class Lookup(StrEnum):
    """Which catalog lookup found a recommendation's edition."""

    SEARCH = "search"  # the free-text search, with or without the language filter
    FALLBACK = "fallback"  # the stricter title + author lookup, after the search missed


class Violation(BaseModel, frozen=True):
    rule: Rule
    index: int | None  # position in the recommendation list; None for list-level rules
    message: str
    evidence: MatchEvidence | None = None  # set on library rules, so evals can audit them


class Unknown(BaseModel, frozen=True):
    """A rule that could not be decided, e.g. no page count. Never counted as a pass."""

    rule: Rule
    index: int
    message: str


class CheckedRecommendation(BaseModel, frozen=True):
    recommendation: Recommendation
    match: CatalogBook | None  # the resolved edition: link, pages, language, cover
    found_by: Lookup | None = None  # None when not found; FALLBACK counts rescued books
    library_match: Book | None  # the profile book it collides with, if any
    library_evidence: MatchEvidence | None = None  # what decided library_match
    cited_titles: list[str]  # profile titles the reason mentions
    exists: Check
    not_in_library: Check
    language: Check
    max_pages: Check
    min_pages: Check = Check.SKIPPED  # default keeps records from before D-026 readable
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
