"""A reader's request: free text interpreted into a soft preference and hard filters."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from book_agent.domain.recommendations import Filters

MAX_REQUEST_CHARS = 500

FilterName = Literal["language", "max_pages", "min_pages"]


class FilterSource(StrEnum):
    TEXT = "text"  # interpreted from the reader's request
    FLAG = "flag"  # given explicitly (CLI flag, later a UI control)


class ConflictingFilters(ValueError):
    """A minimum page count above the maximum: the request can't be met as stated.

    One policy wherever it happens, inside the text or between text and flags: the
    request fails with this error, which names the conflict (D-026). The UI will show it
    as a clarification question.
    """

    def __init__(
        self, min_pages: int, max_pages: int, min_source: FilterSource, max_source: FilterSource
    ) -> None:
        super().__init__(
            f"min_pages {min_pages} (from {min_source.value}) is above "
            f"max_pages {max_pages} (from {max_source.value})"
        )
        self.min_pages = min_pages
        self.max_pages = max_pages


class ExtractedRequest(BaseModel, frozen=True):
    """What the interpreter extracted from the text, literally, before building filters.

    Each value is validated on its own; comparing them (a minimum above the maximum) is
    code's job, in to_parsed, not the model's.
    """

    preference: str | None = None
    language: str | None = Field(default=None, pattern=r"^[a-z]{2}$")
    max_pages: int | None = Field(default=None, gt=0)
    min_pages: int | None = Field(default=None, gt=0)

    def to_parsed(self) -> "ParsedRequest":
        """The preference and filters. Raises ConflictingFilters if min > max."""
        low, high = self.min_pages, self.max_pages
        if low is not None and high is not None and low > high:
            raise ConflictingFilters(low, high, FilterSource.TEXT, FilterSource.TEXT)
        filters = Filters(
            language=self.language, max_pages=self.max_pages, min_pages=self.min_pages
        )
        return ParsedRequest(preference=self.preference, filters=filters)


class ParsedRequest(BaseModel, frozen=True):
    """What the interpreter understood from the request text."""

    preference: str | None  # the soft part, in the reader's own words
    filters: Filters


class ResolvedRequest(BaseModel, frozen=True):
    """The request after merging interpreted filters with explicit ones."""

    text: str | None
    preference: str | None
    filters: Filters
    sources: dict[FilterName, FilterSource]  # one entry per filter that is set


_FILTER_NAMES: tuple[FilterName, ...] = ("language", "max_pages", "min_pages")


def merge_filters(
    interpreted: Filters, flags: Filters
) -> tuple[Filters, dict[FilterName, FilterSource]]:
    """Explicit flags override interpreted values field by field, recording each source.

    Raises ConflictingFilters if the merged bounds contradict each other (a minimum
    above the maximum), e.g. --max-pages 300 against "more than 500 pages" in the text.
    """
    sources: dict[FilterName, FilterSource] = {}
    merged: dict[str, object] = {}
    for name in _FILTER_NAMES:
        flag, text = getattr(flags, name), getattr(interpreted, name)
        merged[name] = flag or text
        if flag:
            sources[name] = FilterSource.FLAG
        elif text:
            sources[name] = FilterSource.TEXT
    low, high = merged["min_pages"], merged["max_pages"]
    if isinstance(low, int) and isinstance(high, int) and low > high:
        raise ConflictingFilters(low, high, sources["min_pages"], sources["max_pages"])
    return Filters.model_validate(merged), sources
