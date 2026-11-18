import pytest
from pydantic import ValidationError

from book_agent.domain.recommendations import Filters
from book_agent.domain.requests import FilterSource, merge_filters

TEXT, FLAG = FilterSource.TEXT, FilterSource.FLAG


def test_interpreted_filters_are_used_when_no_flags() -> None:
    filters, sources = merge_filters(Filters(language="en", max_pages=400), Filters())
    assert filters == Filters(language="en", max_pages=400)
    assert sources == {"language": TEXT, "max_pages": TEXT}


def test_flags_override_field_by_field() -> None:
    filters, sources = merge_filters(Filters(language="en", max_pages=400), Filters(max_pages=250))
    assert filters == Filters(language="en", max_pages=250)
    assert sources == {"language": TEXT, "max_pages": FLAG}


def test_flags_only() -> None:
    filters, sources = merge_filters(Filters(), Filters(language="es"))
    assert filters == Filters(language="es")
    assert sources == {"language": FLAG}


def test_no_filters_anywhere() -> None:
    filters, sources = merge_filters(Filters(), Filters())
    assert filters == Filters()
    assert sources == {}


# --- min_pages (D-026) --------------------------------------------------------------------


def test_min_pages_merges_like_the_other_filters() -> None:
    filters, sources = merge_filters(Filters(min_pages=600), Filters(max_pages=900))
    assert filters == Filters(min_pages=600, max_pages=900)
    assert sources == {"max_pages": FLAG, "min_pages": TEXT}


def test_a_min_flag_overrides_the_interpreted_min() -> None:
    filters, sources = merge_filters(Filters(min_pages=600), Filters(min_pages=200))
    assert filters.min_pages == 200 and sources == {"min_pages": FLAG}


def test_contradicting_bounds_name_their_sources() -> None:
    with pytest.raises(
        ValueError, match=r"min_pages 500 \(from text\) is above max_pages 300 \(from flag\)"
    ):
        merge_filters(Filters(min_pages=500), Filters(max_pages=300))


def test_filters_reject_a_minimum_above_the_maximum() -> None:
    with pytest.raises(ValidationError, match="min_pages 500 is above max_pages 300"):
        Filters(min_pages=500, max_pages=300)
    assert Filters(min_pages=300, max_pages=300).min_pages == 300  # inclusive: equal is fine
