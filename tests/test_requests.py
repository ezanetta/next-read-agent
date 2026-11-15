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
