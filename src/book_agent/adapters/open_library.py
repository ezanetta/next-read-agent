"""Open Library client: implements the WorkResolver port with an injected httpx.AsyncClient.

Answers two questions: which work does this ISBN belong to (WorkResolver), and what is
a work about (WorkDescriptions, D-041)? OpenLibraryCovers finds cover ids through search
(CoverFinder, D-046). Google Books keeps doing search and edition
details. Open Library asks clients to identify themselves with a
User-Agent that includes contact details; user_agent() builds it from settings.
Docs: https://openlibrary.org/dev/docs/api/books
"""

import asyncio
import re
from http import HTTPStatus
from importlib.metadata import version

import httpx
from pydantic import BaseModel, ValidationError

from book_agent.domain.matching import same_work
from book_agent.domain.works import WorkInfo
from book_agent.ports import CoverLookupError, WorkLookupError

BASE_URL = "https://openlibrary.org"
DEFAULT_MAX_CONCURRENCY = 2  # polite: a free, volunteer-run service

_WORK_PREFIX = "/works/"
_WORK_KEY = re.compile(r"^/works/OL\d+W$")
_MAX_SUBJECTS = 15


def user_agent(contact: str) -> str:
    """The User-Agent Open Library asks identified clients for: name/version (contact)."""
    return f"book-agent/{version('book-agent')} ({contact})"


class OpenLibraryWorks:
    """WorkResolver backed by Open Library's ISBN endpoint.

    The httpx client is injected so its lifetime, timeouts and User-Agent are owned by
    the caller (and so tests can mock it). A semaphore limits concurrent requests.
    No retries: a failed lookup is reported and can be retried on the next run.
    """

    def __init__(
        self, client: httpx.AsyncClient, *, max_concurrency: int = DEFAULT_MAX_CONCURRENCY
    ) -> None:
        self._client = client
        self._slots = asyncio.Semaphore(max_concurrency)

    async def work_for_isbn(self, isbn13: str) -> str | None:
        if not isbn13.isdigit():  # also keeps the value safe to put in the path
            raise WorkLookupError(f"not an ISBN-13: {isbn13!r}")
        async with self._slots:
            try:
                # /isbn/{isbn}.json redirects to the edition record (/books/OL...M.json).
                response = await self._client.get(
                    f"{BASE_URL}/isbn/{isbn13}.json", follow_redirects=True
                )
            except httpx.TimeoutException as e:
                raise WorkLookupError("Open Library request timed out") from e
            except httpx.HTTPError as e:
                raise WorkLookupError(f"Open Library request failed: {type(e).__name__}") from e

        if response.status_code == HTTPStatus.NOT_FOUND:
            return None
        if response.status_code != HTTPStatus.OK:
            raise WorkLookupError(f"Open Library returned HTTP {response.status_code}")
        try:
            edition = _Edition.model_validate_json(response.content)
        except ValidationError as e:
            raise WorkLookupError(
                f"Unexpected Open Library response: {e.error_count()} errors"
            ) from e
        return _work_key(edition)

    async def work_info(self, work_key: str) -> WorkInfo | None:
        if not _WORK_KEY.match(work_key):  # also keeps the value safe to put in the path
            raise WorkLookupError(f"not a work key: {work_key!r}")
        async with self._slots:
            try:
                response = await self._client.get(
                    f"{BASE_URL}{work_key}.json", follow_redirects=True
                )
            except httpx.TimeoutException as e:
                raise WorkLookupError("Open Library request timed out") from e
            except httpx.HTTPError as e:
                raise WorkLookupError(f"Open Library request failed: {type(e).__name__}") from e
        if response.status_code == HTTPStatus.NOT_FOUND:
            return None
        if response.status_code != HTTPStatus.OK:
            raise WorkLookupError(f"Open Library returned HTTP {response.status_code}")
        try:
            work = _Work.model_validate_json(response.content)
        except ValidationError as e:
            raise WorkLookupError(
                f"Unexpected Open Library response: {e.error_count()} errors"
            ) from e
        return _work_info(work)


# Goodreads appends series notes in brackets: "Guards! Guards! (Discworld #8)". Search
# without them; same_work ignores them when comparing.
_BRACKETS = re.compile(r"\s*[(\[][^)\]]*[)\]]")
_COVER_SEARCH_FIELDS = "title,author_name,cover_i"
_COVER_SEARCH_RESULTS = 5


class OpenLibraryCovers:
    """CoverFinder backed by Open Library search (title and author fields).

    One request per call and no pacing: the caller (CoverQueue, the owner export) paces
    every request, so the rate holds however many callers there are. No retries.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def cover_id(self, title: str, author: str) -> int | None:
        search_title = _BRACKETS.sub("", title).strip() or title
        try:
            response = await self._client.get(
                f"{BASE_URL}/search.json",
                params={
                    "title": search_title,
                    "author": author,
                    "fields": _COVER_SEARCH_FIELDS,
                    "limit": _COVER_SEARCH_RESULTS,
                },
            )
        except httpx.TimeoutException as e:
            raise CoverLookupError("Open Library request timed out") from e
        except httpx.HTTPError as e:
            raise CoverLookupError(f"Open Library request failed: {type(e).__name__}") from e
        if response.status_code != HTTPStatus.OK:
            raise CoverLookupError(f"Open Library returned HTTP {response.status_code}")
        try:
            results = _SearchResults.model_validate_json(response.content)
        except ValidationError as e:
            raise CoverLookupError(
                f"Unexpected Open Library response: {e.error_count()} errors"
            ) from e
        return next(
            (
                doc.cover_i
                for doc in results.docs
                if doc.cover_i and same_work(title, [author], doc.title, doc.author_name)
            ),
            None,
        )


# --- API response shapes ---------------------------------------------------
# Only the fields we read.


class _Ref(BaseModel):
    key: str


class _Edition(BaseModel):
    works: list[_Ref] = []  # almost always exactly one


class _Text(BaseModel):
    value: str = ""


class _Work(BaseModel):
    # A plain string in older records, {"type": "/type/text", "value": ...} in newer ones.
    description: str | _Text | None = None
    subjects: list[str] = []


class _SearchDoc(BaseModel):
    title: str = ""
    author_name: list[str] = []
    cover_i: int | None = None


class _SearchResults(BaseModel):
    docs: list[_SearchDoc] = []


# --- Mapping to the domain --------------------------------------------------


def _work_key(edition: _Edition) -> str | None:
    """The first work key of the edition, or None if it lists no work."""
    return next((w.key for w in edition.works if w.key.startswith(_WORK_PREFIX)), None)


def _work_info(work: _Work) -> WorkInfo:
    description = (
        work.description.value if isinstance(work.description, _Text) else work.description
    )
    return WorkInfo(
        description=" ".join(description.split()) or None if description else None,
        subjects=tuple(work.subjects[:_MAX_SUBJECTS]),
    )
