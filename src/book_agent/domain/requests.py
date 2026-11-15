"""A reader's request: free text interpreted into a soft preference and hard filters."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel

from book_agent.domain.recommendations import Filters

MAX_REQUEST_CHARS = 500

FilterName = Literal["language", "max_pages"]


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


def merge_filters(
    interpreted: Filters, flags: Filters
) -> tuple[Filters, dict[FilterName, FilterSource]]:
    """Explicit flags override interpreted values field by field, recording each source."""
    sources: dict[FilterName, FilterSource] = {}

    language = flags.language or interpreted.language
    if flags.language:
        sources["language"] = FilterSource.FLAG
    elif interpreted.language:
        sources["language"] = FilterSource.TEXT

    max_pages = flags.max_pages or interpreted.max_pages
    if flags.max_pages:
        sources["max_pages"] = FilterSource.FLAG
    elif interpreted.max_pages:
        sources["max_pages"] = FilterSource.TEXT

    return Filters(language=language, max_pages=max_pages), sources
