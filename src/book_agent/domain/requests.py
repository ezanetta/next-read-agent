"""A reader's request: free text interpreted into a soft preference and hard filters."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel

from book_agent.domain.recommendations import Filters

MAX_REQUEST_CHARS = 500

FilterName = Literal["language", "max_pages", "min_pages"]


class FilterSource(StrEnum):
    TEXT = "text"  # interpreted from the reader's request
    FLAG = "flag"  # given explicitly (CLI flag, later a UI control)


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

    Raises ValueError if the merged bounds contradict each other (a minimum above the
    maximum), e.g. --max-pages 300 against "more than 500 pages" in the text.
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
        raise ValueError(
            f"min_pages {low} (from {sources['min_pages'].value}) is above "
            f"max_pages {high} (from {sources['max_pages'].value})"
        )
    return Filters.model_validate(merged), sources
