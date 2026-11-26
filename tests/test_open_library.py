import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from book_agent.adapters.open_library import (
    BASE_URL,
    OpenLibraryCovers,
    OpenLibraryWorks,
    user_agent,
)
from book_agent.domain.works import WorkInfo
from book_agent.ports import (
    CoverFinder,
    CoverLookupError,
    WorkDescriptions,
    WorkLookupError,
    WorkResolver,
)

ISBN = "9789871165070"  # La metamorfosis, from the real Goodreads export
ISBN_URL = f"{BASE_URL}/isbn/{ISBN}.json"
EDITION_URL = f"{BASE_URL}/books/OL24966425M.json"
EDITION = {
    "key": "/books/OL24966425M",
    "title": "La Metamorfosis",
    "works": [{"key": "/works/OL498556W"}],
}


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


@pytest.fixture
def works(client: httpx.AsyncClient) -> OpenLibraryWorks:
    return OpenLibraryWorks(client)


def test_implements_the_port(works: OpenLibraryWorks) -> None:
    resolver: WorkResolver = works  # pyright checks the Protocol here
    assert resolver is works


async def test_follows_the_isbn_redirect_to_the_edition(
    works: OpenLibraryWorks, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(ISBN_URL).mock(
        return_value=httpx.Response(302, headers={"Location": EDITION_URL})
    )
    respx_mock.get(EDITION_URL).mock(return_value=httpx.Response(200, json=EDITION))
    assert await works.work_for_isbn(ISBN) == "/works/OL498556W"


async def test_direct_edition_response(
    works: OpenLibraryWorks, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(ISBN_URL).mock(return_value=httpx.Response(200, json=EDITION))
    assert await works.work_for_isbn(ISBN) == "/works/OL498556W"


async def test_unknown_isbn_is_none(works: OpenLibraryWorks, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(ISBN_URL).mock(return_value=httpx.Response(404, json={"error": "notfound"}))
    assert await works.work_for_isbn(ISBN) is None


async def test_edition_without_a_work_is_none(
    works: OpenLibraryWorks, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(ISBN_URL).mock(
        return_value=httpx.Response(200, json={"key": "/books/OL1M", "works": [{"key": "/x"}]})
    )
    assert await works.work_for_isbn(ISBN) is None


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_error_statuses_raise(
    works: OpenLibraryWorks, respx_mock: respx.MockRouter, status: int
) -> None:
    respx_mock.get(ISBN_URL).mock(return_value=httpx.Response(status))
    with pytest.raises(WorkLookupError, match=f"HTTP {status}"):
        await works.work_for_isbn(ISBN)


async def test_timeout_raises(works: OpenLibraryWorks, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(ISBN_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(WorkLookupError, match="timed out"):
        await works.work_for_isbn(ISBN)


async def test_network_error_raises(works: OpenLibraryWorks, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(ISBN_URL).mock(side_effect=httpx.ConnectError("no route"))
    with pytest.raises(WorkLookupError, match="ConnectError"):
        await works.work_for_isbn(ISBN)


async def test_malformed_body_raises(works: OpenLibraryWorks, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(ISBN_URL).mock(return_value=httpx.Response(200, text="<html>busy</html>"))
    with pytest.raises(WorkLookupError, match="Unexpected Open Library response"):
        await works.work_for_isbn(ISBN)


async def test_non_isbn_is_rejected_without_a_request(
    works: OpenLibraryWorks, respx_mock: respx.MockRouter
) -> None:
    with pytest.raises(WorkLookupError, match="not an ISBN-13"):
        await works.work_for_isbn("../../authors/OL1A")
    assert not respx_mock.calls


async def test_concurrent_requests_are_limited() -> None:
    in_flight = peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return httpx.Response(200, json=EDITION)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        works = OpenLibraryWorks(client, max_concurrency=2)
        await asyncio.gather(*(works.work_for_isbn(ISBN) for _ in range(6)))
    assert peak == 2


def test_user_agent_names_the_app_its_version_and_the_contact() -> None:
    agent = user_agent("someone@example.com")
    assert agent.startswith("book-agent/")
    assert agent.endswith(" (someone@example.com)")


WORK_URL = f"{BASE_URL}/works/OL498556W.json"


@pytest.mark.parametrize(
    "description",
    [
        "A man wakes up as  an insect.",
        {"type": "/type/text", "value": "A man wakes up as  an insect."},
    ],
)
async def test_work_info_reads_both_description_shapes(
    works: OpenLibraryWorks, respx_mock: respx.MockRouter, description: object
) -> None:
    body = {"description": description, "subjects": [f"s{i}" for i in range(20)]}
    respx_mock.get(WORK_URL).mock(return_value=httpx.Response(200, json=body))
    info = await works.work_info("/works/OL498556W")
    assert info is not None and info.description == "A man wakes up as an insect."
    assert info.subjects == tuple(f"s{i}" for i in range(15))
    described: WorkDescriptions = works  # pyright checks the Protocol here
    assert described is works


async def test_work_info_without_description(
    works: OpenLibraryWorks, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(WORK_URL).mock(return_value=httpx.Response(200, json={"title": "X"}))
    assert await works.work_info("/works/OL498556W") == WorkInfo()


async def test_unknown_work_is_none(works: OpenLibraryWorks, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(WORK_URL).mock(return_value=httpx.Response(404))
    assert await works.work_info("/works/OL498556W") is None


@pytest.mark.parametrize("key", ["OL498556W", "/works/../books/x", "/books/OL1M"])
async def test_work_info_rejects_other_keys(works: OpenLibraryWorks, key: str) -> None:
    with pytest.raises(WorkLookupError, match="not a work key"):
        await works.work_info(key)


async def test_work_info_server_error_raises(
    works: OpenLibraryWorks, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(WORK_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(WorkLookupError, match="503"):
        await works.work_info("/works/OL498556W")


# --- Covers (D-046) ----------------------------------------------------------------

SEARCH_URL = f"{BASE_URL}/search.json"
KING_SORROW_ISBN = "9780063459038"


def test_covers_implement_the_port(client: httpx.AsyncClient) -> None:
    finder: CoverFinder = OpenLibraryCovers(client)
    assert finder is not None


async def test_the_edition_cover_comes_from_the_isbns_edition_record(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    edition_url = f"{BASE_URL}/books/OL1M.json"
    respx_mock.get(f"{BASE_URL}/isbn/{KING_SORROW_ISBN}.json").mock(
        return_value=httpx.Response(302, headers={"Location": edition_url})
    )
    respx_mock.get(edition_url).mock(
        return_value=httpx.Response(200, json={"title": "King Sorrow", "covers": [-1, 15, 16]})
    )
    assert await OpenLibraryCovers(client).edition_cover_id(KING_SORROW_ISBN) == 15


@pytest.mark.parametrize(
    "response", [httpx.Response(404), httpx.Response(200, json={"title": "No covers"})]
)
async def test_an_unknown_or_coverless_edition_is_none(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter, response: httpx.Response
) -> None:
    respx_mock.get(f"{BASE_URL}/isbn/{KING_SORROW_ISBN}.json").mock(return_value=response)
    assert await OpenLibraryCovers(client).edition_cover_id(KING_SORROW_ISBN) is None


async def test_an_edition_lookup_rejects_anything_but_digits(client: httpx.AsyncClient) -> None:
    with pytest.raises(CoverLookupError):
        await OpenLibraryCovers(client).edition_cover_id("../search")


def doc(title: str, author: str, cover: int | None, edition: tuple[str, int | None] | None):
    editions = {"docs": [{"title": edition[0], "cover_i": edition[1]}]} if edition else {}
    return {"title": title, "author_name": [author], "cover_i": cover, "editions": editions}


async def test_search_prefers_an_edition_whose_title_matches_over_the_works_cover(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    # The work's cover is the Portuguese edition; the best-matching edition is English.
    route = respx_mock.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, json={"docs": [doc("King Sorrow", "Joe Hill", 99, ("King Sorrow", 42))]}
        )
    )
    assert await OpenLibraryCovers(client).search_cover_id("King Sorrow", "Joe Hill") == 42
    params = route.calls.last.request.url.params
    assert params["fields"] == "title,author_name,cover_i,editions,editions.title,editions.cover_i"


async def test_search_drops_the_series_and_skips_other_works(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "docs": [
                    doc("The Science of Discworld", "Terry Pratchett", 1, ("The Science", 1)),
                    doc("Guards! Guards!", "Terry Pratchett", 50, ("Guards! Guards!", 42)),
                ]
            },
        )
    )
    title = "Guards! Guards! (Discworld #8, City Watch #1)"
    assert await OpenLibraryCovers(client).search_cover_id(title, "Terry Pratchett") == 42
    params = route.calls.last.request.url.params
    assert params["title"] == "Guards! Guards!" and params["author"] == "Terry Pratchett"


async def test_search_falls_back_to_the_works_cover_when_no_edition_title_matches(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, json={"docs": [doc("King Sorrow", "Joe Hill", 99, ("O Sacrifício", 77))]}
        )
    )
    assert await OpenLibraryCovers(client).search_cover_id("King Sorrow", "Joe Hill") == 99


async def test_search_without_the_same_work_is_none(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"docs": [doc("Dune", "Ann Other", 9, None)]})
    )
    assert await OpenLibraryCovers(client).search_cover_id("Dune", "Frank Herbert") is None


@pytest.mark.parametrize(
    "response",
    [httpx.Response(429), httpx.Response(503), httpx.Response(200, content=b"not json")],
)
async def test_cover_lookup_failures_raise(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter, response: httpx.Response
) -> None:
    respx_mock.get(SEARCH_URL).mock(return_value=response)
    respx_mock.get(f"{BASE_URL}/isbn/{KING_SORROW_ISBN}.json").mock(return_value=response)
    finder = OpenLibraryCovers(client)
    with pytest.raises(CoverLookupError):
        await finder.search_cover_id("Dune", "Frank Herbert")
    with pytest.raises(CoverLookupError):
        await finder.edition_cover_id(KING_SORROW_ISBN)


async def test_cover_lookup_timeouts_raise(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(SEARCH_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(CoverLookupError, match="timed out"):
        await OpenLibraryCovers(client).search_cover_id("Dune", "Frank Herbert")
