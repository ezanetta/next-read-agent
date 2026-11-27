"""R-006: no title, request text or profile id in the logs, whatever goes wrong."""

import json
import logging

import httpx
import pytest
import respx

from book_agent import log_privacy
from book_agent.adapters.open_library import BASE_URL, OpenLibraryCovers
from book_agent.domain.profiles import StoredProfile
from book_agent.ports import CoverLookupError
from tests.api_support import NOW, client, services
from tests.fakes import FakeCoverFinder, text_response
from tests.library_data import PROFILE

SECRET = "SECRET"  # every title, author and request below contains it


def secret_csv() -> bytes:
    header = "Book Id,Title,Author,My Rating,Exclusive Shelf"
    rows = [f"{i},{SECRET} Title {i},{SECRET} Author {i},4,read" for i in range(6)]
    rows.append(f"90,{SECRET} Bad Rating,{SECRET} Someone,6,read")  # skipped: rating > 5
    rows.append(f"91,{SECRET} Odd Shelf,{SECRET} Someone,0,{SECRET}-shelf")  # skipped
    return "\n".join([header, *rows]).encode()


class BuggyFinder(FakeCoverFinder):
    """Cover lookups that fail every way: an expected error and a bug."""

    async def search_cover_id(self, title: str, author: str) -> int | None:
        if title.endswith("Title 1"):
            raise RuntimeError(f"bug while handling {title} by {author}")
        return await super().search_cover_id(title, author)


def assert_private(caplog: pytest.LogCaptureFixture, *secrets: str) -> None:
    assert caplog.records, "nothing was logged: the test would prove nothing"
    for secret in (SECRET, *secrets):
        assert secret not in caplog.text, f"{secret!r} leaked into the logs"


async def test_an_upload_and_its_cover_lookups_log_no_library_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    failure = CoverLookupError("Open Library returned HTTP 503")
    finder = BuggyFinder({f"{SECRET} Title 0": failure, f"{SECRET} Title 2": 7})
    api = services(covers=finder)
    assert api.covers is not None
    async with client(api) as c:
        created = await c.post("/profiles/goodreads", files={"file": ("x.csv", secret_csv())})
        profile_id = created.json()["id"]
        await c.get(f"/profiles/{profile_id}/books")
        await api.covers.join()
        await api.covers.aclose()

    assert created.status_code == 201
    assert "Skipping row" in caplog.text  # the parser's warnings were captured
    assert "cover lookup failed (profile" in caplog.text  # the expected failure
    assert "cover lookup failed unexpectedly" in caplog.text  # and the bug
    assert log_privacy.ref(profile_id) in caplog.text  # ids appear only as hashes
    assert_private(caplog, profile_id)


async def test_a_failing_recommendation_run_logs_no_library_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    library = PROFILE.model_copy(
        update={
            "books": [b.model_copy(update={"title": f"{SECRET} {b.title}"}) for b in PROFILE.books]
        }
    )
    preset = {"p": StoredProfile(id="p", source="sample", profile=library, created_at=NOW)}
    parsed = text_response(
        json.dumps({"preference": f"{SECRET} wish", "language": None, "max_pages": None})
    )
    bug = RuntimeError(f"bug quoting {library.books[0].title}")
    async with client(services([parsed, bug], presets=preset)) as c:
        response = await c.post(
            "/recommendations", json={"profile_id": "p", "request": f"{SECRET} request text"}
        )
    assert '"type":"failed"' in response.text.replace(" ", "")
    assert "recommendation run failed unexpectedly" in caplog.text
    assert_private(caplog)


async def test_httpx_request_urls_are_kept_out_of_the_logs(
    caplog: pytest.LogCaptureFixture, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(f"{BASE_URL}/search.json").mock(
        return_value=httpx.Response(200, json={"docs": []})
    )
    caplog.set_level(logging.DEBUG)
    httpx_logger = logging.getLogger("httpx")
    async with httpx.AsyncClient() as http:
        finder = OpenLibraryCovers(http)
        httpx_logger.setLevel(logging.NOTSET)  # httpx's default: the URL, title included
        await finder.search_cover_id(f"{SECRET} Leaky", "Author")
        assert SECRET in caplog.text
        caplog.clear()
        log_privacy.install()
        await finder.search_cover_id(f"{SECRET} Quiet", "Author")
    assert SECRET not in caplog.text


@pytest.mark.parametrize(
    ("path", "logged"),
    [
        ("/books/search?q=secret+title", "/books/search"),
        ("/profiles/AbC123_xyz", "/profiles/{id}"),
        ("/profiles/AbC123_xyz/books?shelf=read", "/profiles/{id}/books"),
        ("/profiles/goodreads", "/profiles/goodreads"),
        ("/profiles/presets", "/profiles/presets"),
        ("/recommendations", "/recommendations"),
    ],
)
def test_access_log_paths_lose_queries_and_profile_ids(path: str, logged: str) -> None:
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", path, "1.1", 200), None,
    )  # fmt: skip
    assert log_privacy.AccessLogRedactor().filter(record)
    assert record.getMessage() == f'127.0.0.1:5000 - "GET {logged} HTTP/1.1" 200'


def test_installing_twice_adds_one_access_log_filter() -> None:
    log_privacy.install()
    log_privacy.install()
    access = logging.getLogger("uvicorn.access")
    assert sum(isinstance(f, log_privacy.AccessLogRedactor) for f in access.filters) == 1
    assert logging.getLogger("httpx").level == logging.WARNING
