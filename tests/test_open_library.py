import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from book_agent.adapters.open_library import BASE_URL, OpenLibraryWorks, user_agent
from book_agent.ports import WorkLookupError, WorkResolver

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
