import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import respx

from book_agent.adapters.google_books import (
    BASE_URL,
    RETRY_JITTER_S,
    GoogleBooksSearch,
    fielded_query,
    retry_jitter,
)
from book_agent.domain.models import CatalogBook
from book_agent.ports import BookSearch, BookSearchError

FIXTURES = Path(__file__).parent / "fixtures" / "google_books"
VOLUMES_URL = f"{BASE_URL}/volumes"


async def no_sleep(seconds: float) -> None:
    """Retries in tests don't wait."""


def load(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


@pytest.fixture
def books(client: httpx.AsyncClient) -> GoogleBooksSearch:
    return GoogleBooksSearch(client, sleep=no_sleep)


@pytest.fixture
def search_route(respx_mock: respx.MockRouter) -> respx.Route:
    return respx_mock.get(VOLUMES_URL).mock(
        return_value=httpx.Response(200, json=load("search_results.json"))
    )


async def search_by_id(books: GoogleBooksSearch) -> dict[str, CatalogBook]:
    return {b.source_id: b for b in await books.search("dune")}


def test_adapter_satisfies_the_protocol(books: GoogleBooksSearch) -> None:
    search: BookSearch = books  # checked by pyright
    assert search is books


# --- search: mapping and quirks ----------------------------------------------


async def test_maps_a_complete_volume(books: GoogleBooksSearch, search_route: respx.Route) -> None:
    dune = (await search_by_id(books))["B1hSG45JCX4C"]
    assert dune == CatalogBook.model_validate(
        {
            "source_id": "B1hSG45JCX4C",
            "title": "Dune",
            "authors": ("Frank Herbert",),
            "isbn": "0441013597",
            "isbn13": "9780441013593",
            "pages": 896,
            "language": "en",
            "categories": ("Fiction",),
            "description": (
                "Set on the desert planet Arrakis, Dune is the story of "
                "Paul Atreides & his family.\n"
                "A stunning blend of adventure,\nmysticism and politics — a classic."
            ),
            "year": 1965,
            "cover_url": "https://books.google.com/books/content?id=B1hSG45JCX4C"
            "&printsec=frontcover&img=1&zoom=1&source=gbs_api",
            "link": "https://books.google.com/books/about/Dune.html?id=B1hSG45JCX4C",
        }
    )


async def test_extracts_year_from_every_date_format(
    books: GoogleBooksSearch, search_route: respx.Route
) -> None:
    by_id = await search_by_id(books)
    assert by_id["B1hSG45JCX4C"].year == 1965  # "1965-08-01"
    assert by_id["esDuneSpanish"].year == 2019  # "2019-03"
    assert by_id["minimalVol01"].year == 2001  # "2001"


async def test_isbn13_only_comes_from_isbn_13_identifiers(
    books: GoogleBooksSearch, search_route: respx.Route
) -> None:
    spanish = (await search_by_id(books))["esDuneSpanish"]  # OTHER + ISBN_10 only
    assert spanish.isbn == "8466353309"
    assert spanish.isbn13 is None


async def test_thumbnail_is_switched_to_https(
    books: GoogleBooksSearch, search_route: respx.Route
) -> None:
    dune = (await search_by_id(books))["B1hSG45JCX4C"]
    assert dune.cover_url is not None
    assert dune.cover_url.scheme == "https"


async def test_link_falls_back_to_info_link(
    books: GoogleBooksSearch, search_route: respx.Route
) -> None:
    spanish = (await search_by_id(books))["esDuneSpanish"]
    assert str(spanish.link) == "https://books.google.com/books?id=esDuneSpanish&source=gbs_api"


async def test_volume_with_almost_no_data(
    books: GoogleBooksSearch, search_route: respx.Route
) -> None:
    minimal = (await search_by_id(books))["minimalVol01"]
    assert minimal.title == "Dune Notes"
    assert minimal.authors == ()
    assert minimal.categories == ()
    assert minimal.isbn is None
    assert minimal.isbn13 is None
    assert minimal.pages is None
    assert minimal.language is None
    assert minimal.description is None
    assert minimal.cover_url is None


async def test_unusable_volume_is_skipped_with_a_compact_debug_line(
    books: GoogleBooksSearch, search_route: respx.Route, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG, logger="book_agent.adapters.google_books"):
        results = await books.search("dune")
    assert [b.source_id for b in results] == ["B1hSG45JCX4C", "esDuneSpanish", "minimalVol01"]
    [record] = caplog.records
    assert record.levelno == logging.DEBUG
    message = record.getMessage()
    assert message.startswith("Skipping Google Books volume 'noTitleVol01': title:")
    assert "\n" not in message and "errors.pydantic.dev" not in message


async def test_no_matches_returns_empty_list(
    books: GoogleBooksSearch, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(VOLUMES_URL).mock(
        return_value=httpx.Response(200, json=load("search_empty.json"))
    )
    assert await books.search("zzzz no such book") == []


# --- search: request parameters ----------------------------------------------


async def test_sends_query_and_books_only(
    books: GoogleBooksSearch, search_route: respx.Route
) -> None:
    await books.search("dune frank herbert")
    params = search_route.calls.last.request.url.params
    assert params["q"] == "dune frank herbert"
    assert params["printType"] == "books"
    assert params["maxResults"] == "10"
    assert "langRestrict" not in params
    assert "key" not in params


async def test_language_filter_drops_other_known_languages(
    books: GoogleBooksSearch, search_route: respx.Route
) -> None:
    # The fixture has an "en" volume, an "es" volume and one with no language.
    results = await books.search("dune", language="es")
    assert [b.source_id for b in results] == ["esDuneSpanish", "minimalVol01"]


async def test_no_language_filter_keeps_every_language(
    books: GoogleBooksSearch, search_route: respx.Route
) -> None:
    languages = [b.language for b in await books.search("dune")]
    assert languages == ["en", "es", None]


async def test_sends_language_restriction(
    books: GoogleBooksSearch, search_route: respx.Route
) -> None:
    await books.search("dune", language="es")
    assert search_route.calls.last.request.url.params["langRestrict"] == "es"


@pytest.mark.parametrize(("requested", "sent"), [(100, "40"), (40, "40"), (5, "5"), (0, "1")])
async def test_clamps_max_results(
    books: GoogleBooksSearch, search_route: respx.Route, requested: int, sent: str
) -> None:
    await books.search("dune", max_results=requested)
    assert search_route.calls.last.request.url.params["maxResults"] == sent


async def test_sends_api_key_when_present(
    client: httpx.AsyncClient, search_route: respx.Route
) -> None:
    await GoogleBooksSearch(client, api_key="test-key").search("dune")
    assert search_route.calls.last.request.url.params["key"] == "test-key"


# --- get_details -------------------------------------------------------------


async def test_get_details_returns_the_volume(
    books: GoogleBooksSearch, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(f"{VOLUMES_URL}/zyTCAlFPjgYC").mock(
        return_value=httpx.Response(200, json=load("volume_details.json"))
    )
    book = await books.get_details("zyTCAlFPjgYC")
    assert book is not None
    assert book.title == "The Google story"
    assert book.authors == ("David A. Vise", "Mark Malseed")
    assert book.isbn13 == "9780553804577"
    assert book.year == 2005
    assert book.pages == 207


async def test_get_details_returns_none_when_not_found(
    books: GoogleBooksSearch, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(f"{VOLUMES_URL}/missing").mock(return_value=httpx.Response(404))
    assert await books.get_details("missing") is None


async def test_get_details_with_blank_id_makes_no_request(
    books: GoogleBooksSearch, respx_mock: respx.MockRouter
) -> None:
    assert await books.get_details("  ") is None
    assert not respx_mock.calls


async def test_get_details_escapes_the_id(
    books: GoogleBooksSearch, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(f"{VOLUMES_URL}/a%2Fb").mock(return_value=httpx.Response(404))
    await books.get_details("a/b")
    assert route.called


# --- errors ------------------------------------------------------------------


async def test_quota_error_raises_with_the_api_message(
    books: GoogleBooksSearch, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(VOLUMES_URL).mock(return_value=httpx.Response(429, json=load("error_429.json")))
    with pytest.raises(BookSearchError, match=r"HTTP 429: Quota exceeded"):
        await books.search("dune")


async def test_non_json_error_body_still_raises(
    books: GoogleBooksSearch, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(VOLUMES_URL).mock(return_value=httpx.Response(502, text="<html>Bad Gateway"))
    with pytest.raises(BookSearchError, match=r"^Google Books returned HTTP 502$"):
        await books.search("dune")


async def test_server_error_on_details_raises(
    books: GoogleBooksSearch, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(f"{VOLUMES_URL}/vol").mock(return_value=httpx.Response(503))
    with pytest.raises(BookSearchError, match="HTTP 503"):
        await books.get_details("vol")


async def test_timeout_raises(books: GoogleBooksSearch, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(VOLUMES_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(BookSearchError, match="timed out"):
        await books.search("dune")


async def test_network_error_raises(books: GoogleBooksSearch, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(VOLUMES_URL).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(BookSearchError, match="request failed: ConnectError"):
        await books.search("dune")


async def test_malformed_response_raises(
    books: GoogleBooksSearch, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(VOLUMES_URL).mock(return_value=httpx.Response(200, text="not json"))
    with pytest.raises(BookSearchError, match="Unexpected Google Books response"):
        await books.search("dune")


async def test_errors_never_leak_the_api_key(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    books = GoogleBooksSearch(client, api_key="super-secret-key", sleep=no_sleep)
    respx_mock.get(VOLUMES_URL).mock(
        side_effect=[
            httpx.Response(429, json=load("error_429.json")),  # not retried
            httpx.ReadTimeout("slow"),
            httpx.ReadTimeout("still slow"),  # the retry
            httpx.ConnectError("down"),
            httpx.ConnectError("still down"),  # the retry
        ]
    )
    for _ in range(3):
        with pytest.raises(BookSearchError) as error:
            await books.search("dune")
        assert "super-secret-key" not in str(error.value)


@pytest.mark.parametrize(
    ("query", "sent"),
    [
        (
            "The News from Ireland & Other Stories William Trevor",
            "The News from Ireland and Other Stories William Trevor",
        ),
        ("Pride&Prejudice", "Pride and Prejudice"),
        ("Dune Frank Herbert", "Dune Frank Herbert"),
    ],
)
async def test_ampersand_is_sent_as_and(
    books: GoogleBooksSearch, search_route: respx.Route, query: str, sent: str
) -> None:
    # F-007: a literal "&" made Google Books rank reference works above the book.
    await books.search(query)
    assert search_route.calls.last.request.url.params["q"] == sent


@pytest.mark.parametrize(
    ("title", "author", "query"),
    [
        (
            "The News from Ireland & Other Stories",
            "William Trevor",
            'intitle:"The News from Ireland" inauthor:Trevor',
        ),
        (
            "Sapiens: A Brief History of Humankind",
            "Yuval Noah Harari",
            'intitle:"Sapiens" inauthor:Harari',
        ),
        (
            "The Little Prince",
            "Antoine de Saint-Exupéry",
            'intitle:"The Little Prince" inauthor:"Saint-Exupéry"',
        ),
        ('The "Odd" Book (Series, #2)', "A. Writer", 'intitle:"The Odd Book" inauthor:Writer'),
    ],
)
def test_fielded_query(title: str, author: str, query: str) -> None:
    assert fielded_query(title, author) == query


async def test_find_sends_the_fielded_query(
    books: GoogleBooksSearch, search_route: respx.Route
) -> None:
    await books.find("The News from Ireland & Other Stories", "William Trevor", max_results=5)
    params = search_route.calls.last.request.url.params
    assert params["q"] == 'intitle:"The News from Ireland" inauthor:Trevor'
    assert params["maxResults"] == "5"


# --- Retry once on 5xx and network errors (D-027) -----------------------------------------


class Sleeps:
    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


def retrying(client: httpx.AsyncClient, sleeps: Sleeps) -> GoogleBooksSearch:
    return GoogleBooksSearch(client, retry_delay_s=0.5, jitter=lambda: 0.1, sleep=sleeps)


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_a_5xx_is_retried_once_after_a_pause(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter, status: int
) -> None:
    sleeps = Sleeps()
    route = respx_mock.get(VOLUMES_URL).mock(
        side_effect=[
            httpx.Response(status, json={"error": {"message": "Service temporarily unavailable."}}),
            httpx.Response(200, json=load("search_results.json")),
        ]
    )
    results = await retrying(client, sleeps).search("dune")
    assert len(results) == 3
    assert route.call_count == 2
    assert sleeps.waits == [0.6]  # delay plus jitter


@pytest.mark.parametrize("error", [httpx.ReadTimeout("slow"), httpx.ConnectError("down")])
async def test_a_network_error_is_retried_once(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter, error: Exception
) -> None:
    sleeps = Sleeps()
    route = respx_mock.get(VOLUMES_URL).mock(
        side_effect=[error, httpx.Response(200, json=load("search_results.json"))]
    )
    assert len(await retrying(client, sleeps).search("dune")) == 3
    assert route.call_count == 2 and len(sleeps.waits) == 1


async def test_a_second_5xx_is_reported(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    unavailable = {"error": {"message": "Service temporarily unavailable."}}
    route = respx_mock.get(VOLUMES_URL).mock(
        side_effect=[httpx.Response(503, json=unavailable), httpx.Response(503, json=unavailable)]
    )
    with pytest.raises(BookSearchError, match="HTTP 503: Service temporarily unavailable"):
        await retrying(client, Sleeps()).search("dune")
    assert route.call_count == 2  # one retry, not more


@pytest.mark.parametrize("status", [400, 403, 404, 429])
async def test_4xx_answers_are_not_retried(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter, status: int
) -> None:
    sleeps = Sleeps()
    route = respx_mock.get(f"{VOLUMES_URL}/abc").mock(
        return_value=httpx.Response(status, json={"error": {"message": "no"}})
    )
    books = retrying(client, sleeps)
    if status == 404:
        assert await books.get_details("abc") is None
    else:
        with pytest.raises(BookSearchError):
            await books.get_details("abc")
    assert route.call_count == 1 and sleeps.waits == []


async def test_details_and_find_are_retried_too(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    detail = respx_mock.get(f"{VOLUMES_URL}/abc").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=load("volume_details.json"))]
    )
    search = respx_mock.get(VOLUMES_URL).mock(
        side_effect=[httpx.Response(502), httpx.Response(200, json=load("search_results.json"))]
    )
    books = retrying(client, Sleeps())
    assert await books.get_details("abc") is not None
    assert await books.find("Dune", "Frank Herbert")
    assert detail.call_count == 2 and search.call_count == 2


def test_jitter_stays_small() -> None:
    assert all(0 <= retry_jitter() <= RETRY_JITTER_S for _ in range(100))
