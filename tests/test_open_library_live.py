"""Live tests against the real Open Library API. Excluded by default.

Run with: uv run pytest -m live tests/test_open_library_live.py
Needs OPEN_LIBRARY_CONTACT in .env. Two requests (plus their redirects).
"""

import httpx
import pytest
from pydantic import ValidationError

from book_agent.adapters.open_library import OpenLibraryWorks
from book_agent.config import get_settings
from book_agent.wiring import build_open_library_http

pytestmark = pytest.mark.live

METAMORPHOSIS_WORK = "/works/OL498556W"


@pytest.fixture
def http() -> httpx.AsyncClient:
    try:
        client = build_open_library_http(get_settings())
    except ValidationError:
        client = None
    if client is None:
        pytest.skip("OPEN_LIBRARY_CONTACT is not configured")
    return client


async def test_spanish_and_english_editions_share_the_kafka_work(http: httpx.AsyncClient) -> None:
    # F-003: the Spanish edition from the real export and a Bantam English edition.
    async with http:
        works = OpenLibraryWorks(http)
        assert await works.work_for_isbn("9789871165070") == METAMORPHOSIS_WORK
        assert await works.work_for_isbn("9780553213690") == METAMORPHOSIS_WORK
