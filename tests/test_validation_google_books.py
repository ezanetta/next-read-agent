"""The validator with the real Google Books adapter, on recorded API responses.

Regression for F-007: a real book rejected as not found because the free-text search
ranked only reference works. The fixtures are real responses recorded on 2026-11-18.
"""

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import respx

from book_agent.adapters.google_books import BASE_URL, GoogleBooksSearch
from book_agent.baseline import BaselineResult
from book_agent.cli import format_result
from book_agent.domain.recommendations import Check, Filters, Lookup, Rule
from book_agent.domain.validation import validate_recommendations
from tests.library_data import PROFILE, rec

FIXTURES = Path(__file__).parent / "fixtures" / "google_books"
VOLUMES_URL = f"{BASE_URL}/volumes"
TREVOR = rec("The News from Ireland & Other Stories", "William Trevor", "Quiet, like Ficciones.")
# What the adapter sends for the validator's search ("&" as "and", see clean_query), and
# for the fallback (see fielded_query).
SEARCH_QUERY = "The News from Ireland and Other Stories William Trevor"
FALLBACK_QUERY = 'intitle:"The News from Ireland" inauthor:Trevor'


async def _no_sleep(seconds: float) -> None:
    """Retries in tests don't wait."""


def recorded(name: str) -> httpx.Response:
    return httpx.Response(200, json=json.loads((FIXTURES / name).read_text(encoding="utf-8")))


@pytest.fixture
async def books() -> AsyncIterator[GoogleBooksSearch]:
    async with httpx.AsyncClient() as client:
        yield GoogleBooksSearch(client, sleep=_no_sleep)


async def test_fallback_rescues_a_book_the_search_ranked_out(
    books: GoogleBooksSearch, respx_mock: respx.MockRouter
) -> None:
    # The search returns only reference works that mention the book (recorded for the
    # "&" query); the fallback finds the book itself.
    search = respx_mock.get(VOLUMES_URL, params__contains={"q": SEARCH_QUERY}).mock(
        return_value=recorded("trevor_ampersand_query.json")
    )
    fallback = respx_mock.get(VOLUMES_URL, params__contains={"q": FALLBACK_QUERY}).mock(
        return_value=recorded("trevor_intitle_main.json")
    )

    result = await validate_recommendations([TREVOR], PROFILE, Filters(), books)

    item = result.items[0]
    assert item.exists is Check.PASS
    assert item.found_by is Lookup.FALLBACK
    assert item.match is not None
    assert item.match.title == "The News from Ireland & Other Stories"
    assert item.match.authors == ("William Trevor",)
    assert Rule.NOT_FOUND not in [v.rule for v in result.violations]
    assert search.call_count == 1 and fallback.call_count == 1


async def test_without_the_fallback_hit_the_book_is_not_found(
    books: GoogleBooksSearch, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(VOLUMES_URL, params__contains={"q": SEARCH_QUERY}).mock(
        return_value=recorded("trevor_ampersand_query.json")
    )
    respx_mock.get(VOLUMES_URL, params__contains={"q": FALLBACK_QUERY}).mock(
        return_value=httpx.Response(200, json={"kind": "books#volumes", "totalItems": 0})
    )

    result = await validate_recommendations([TREVOR], PROFILE, Filters(), books)

    assert result.items[0].exists is Check.FAIL
    assert (Rule.NOT_FOUND, 0) in [(v.rule, v.index) for v in result.violations]


async def test_report_says_when_the_fallback_found_the_book(
    books: GoogleBooksSearch, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(VOLUMES_URL, params__contains={"q": SEARCH_QUERY}).mock(
        return_value=recorded("trevor_ampersand_query.json")
    )
    respx_mock.get(VOLUMES_URL, params__contains={"q": FALLBACK_QUERY}).mock(
        return_value=recorded("trevor_intitle_main.json")
    )
    validation = await validate_recommendations([TREVOR], PROFILE, Filters(), books)
    report = format_result(BaselineResult(prompt_id="baseline_v1", validation=validation))
    assert "(found by the fallback lookup)" in report


# --- Séneca (F-012, D-029): the book exists only as combined volumes ----------------------

SENECA = rec("Sobre la Brevedad de la Vida", "Lucio Anneo Séneca", "Like Ficciones, but essays.")
SENECA_QUERY = "Sobre la Brevedad de la Vida Lucio Anneo Séneca"
SENECA_FALLBACK = 'intitle:"Sobre la Brevedad de la Vida" inauthor:Séneca'


async def test_a_combined_volume_with_a_dash_counts_as_the_edition(
    books: GoogleBooksSearch, respx_mock: respx.MockRouter
) -> None:
    # Before D-029 all 5 results failed title matching and the book was NOT_FOUND.
    respx_mock.get(VOLUMES_URL, params__contains={"q": SENECA_QUERY}).mock(
        return_value=recorded("seneca_search.json")
    )
    result = await validate_recommendations([SENECA], PROFILE, Filters(), books)

    item = result.items[0]
    assert item.exists is Check.PASS and item.found_by is Lookup.SEARCH
    assert item.match is not None
    assert item.match.title == "Sobre la Brevedad de la Vida - Sobre la Felicidad"
    assert Rule.NOT_FOUND not in [v.rule for v in result.violations]


async def test_a_combined_volume_joined_by_a_period_counts_too(
    books: GoogleBooksSearch, respx_mock: respx.MockRouter
) -> None:
    # Only the "A. B" volumes from the fallback: the search ranked nothing usable.
    respx_mock.get(VOLUMES_URL, params__contains={"q": SENECA_QUERY}).mock(
        return_value=httpx.Response(200, json={"kind": "books#volumes", "totalItems": 0})
    )
    fallback = json.loads((FIXTURES / "seneca_fallback.json").read_text(encoding="utf-8"))
    fallback["items"] = [i for i in fallback["items"] if ". " in i["volumeInfo"]["title"]]
    respx_mock.get(VOLUMES_URL, params__contains={"q": SENECA_FALLBACK}).mock(
        return_value=httpx.Response(200, json=fallback)
    )
    result = await validate_recommendations([SENECA], PROFILE, Filters(), books)

    item = result.items[0]
    assert item.exists is Check.PASS and item.found_by is Lookup.FALLBACK
    assert item.match is not None and ". Sobre la brevedad de la vida" in item.match.title


async def test_other_translations_of_the_title_still_dont_match(
    books: GoogleBooksSearch, respx_mock: respx.MockRouter
) -> None:
    # Known gap (D-029): "De la brevedad de la vida" is the same work, but existence is
    # decided by catalog titles, never by the same-work judge.
    translations = json.loads((FIXTURES / "seneca_search.json").read_text(encoding="utf-8"))
    translations["items"] = [
        i
        for i in translations["items"]
        if i["volumeInfo"]["title"].lower().startswith(("de la", "la "))
    ]
    respx_mock.get(VOLUMES_URL).mock(return_value=httpx.Response(200, json=translations))
    result = await validate_recommendations([SENECA], PROFILE, Filters(), books)
    assert result.items[0].exists is Check.FAIL
