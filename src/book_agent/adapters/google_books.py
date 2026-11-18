"""Google Books API client: implements the BookSearch port with an injected httpx.AsyncClient.

Used live only as an agent fallback tool and for the UI book search.
Docs: https://developers.google.com/books/docs/v1/reference/volumes
"""

import contextlib
import logging
import re
from html import unescape
from html.parser import HTMLParser
from http import HTTPStatus
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic.alias_generators import to_camel

from book_agent.domain.models import CatalogBook
from book_agent.ports import BookSearchError
from book_agent.pydantic_errors import describe

logger = logging.getLogger(__name__)

BASE_URL = "https://www.googleapis.com/books/v1"
MAX_RESULTS_LIMIT = 40  # the API rejects maxResults above 40

_YEAR_PATTERN = re.compile(r"^(\d{4})")
_AMPERSAND = re.compile(r"\s*&\s*")


def clean_query(query: str) -> str:
    """The query as Google Books ranks it best: "&" spelled "and".

    A literal "&" pushes the book itself out of the results: "The News from Ireland &
    Other Stories William Trevor" returned only reference works, while the same query
    with "and" ranked the book first (F-007).
    """
    return _AMPERSAND.sub(" and ", query).strip()


class GoogleBooksSearch:
    """BookSearch backed by the Google Books API.

    The httpx client is injected so its lifetime, timeouts and transport are owned by
    the caller (and so tests can mock it). Without an API key the API still works,
    but at a much lower quota.
    """

    def __init__(self, client: httpx.AsyncClient, api_key: str | None = None) -> None:
        self._client = client
        self._api_key = api_key

    async def search(
        self, query: str, *, language: str | None = None, max_results: int = 10
    ) -> list[CatalogBook]:
        params: dict[str, str | int] = {
            "q": clean_query(query),
            "maxResults": max(1, min(max_results, MAX_RESULTS_LIMIT)),
            "printType": "books",
        }
        if language:
            params["langRestrict"] = language

        response = await self._get(f"{BASE_URL}/volumes", params)
        self._raise_for_status(response)
        payload = _parse(_VolumesResponse, response)
        books = [book for volume in payload.items if (book := _to_catalog_book(volume))]
        # langRestrict is not reliable: drop books tagged with another language.
        # Unknown languages pass through; that policy belongs to the domain validator.
        return [b for b in books if language is None or b.language in (None, language)]

    async def get_details(self, volume_id: str) -> CatalogBook | None:
        if not volume_id.strip():
            return None
        response = await self._get(f"{BASE_URL}/volumes/{quote(volume_id, safe='')}", {})
        if response.status_code == HTTPStatus.NOT_FOUND:
            return None
        self._raise_for_status(response)
        return _to_catalog_book(_parse(_Volume, response))

    async def _get(self, url: str, params: dict[str, str | int]) -> httpx.Response:
        if self._api_key:
            params = params | {"key": self._api_key}
        # Error messages never include the URL: it carries the API key.
        try:
            return await self._client.get(url, params=params)
        except httpx.TimeoutException as e:
            raise BookSearchError("Google Books request timed out") from e
        except httpx.HTTPError as e:
            raise BookSearchError(f"Google Books request failed: {type(e).__name__}") from e

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code == HTTPStatus.OK:
            return
        detail = ""
        with contextlib.suppress(ValidationError):  # error body is best-effort
            detail = f": {_ErrorResponse.model_validate_json(response.content).error.message}"
        raise BookSearchError(f"Google Books returned HTTP {response.status_code}{detail}")


# --- API response shapes ---------------------------------------------------
# Only the fields we read. Everything is optional because the API omits fields freely.


class _ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class _IndustryIdentifier(_ApiModel):
    type: str
    identifier: str


class _ImageLinks(_ApiModel):
    thumbnail: str | None = None
    small_thumbnail: str | None = None


class _VolumeInfo(_ApiModel):
    title: str | None = None
    authors: list[str] = []
    published_date: str | None = None
    description: str | None = None
    industry_identifiers: list[_IndustryIdentifier] = []
    page_count: int | None = None
    categories: list[str] = []
    image_links: _ImageLinks | None = None
    language: str | None = None
    info_link: str | None = None
    canonical_volume_link: str | None = None


class _Volume(_ApiModel):
    id: str
    volume_info: _VolumeInfo = _VolumeInfo()


class _VolumesResponse(_ApiModel):
    total_items: int = 0
    items: list[_Volume] = []  # omitted entirely when nothing matches


class _ErrorDetail(_ApiModel):
    message: str


class _ErrorResponse(_ApiModel):
    error: _ErrorDetail


def _parse[M: BaseModel](model: type[M], response: httpx.Response) -> M:
    try:
        return model.model_validate_json(response.content)
    except ValidationError as e:
        raise BookSearchError(f"Unexpected Google Books response: {e.error_count()} errors") from e


# --- Mapping to the domain --------------------------------------------------


def _to_catalog_book(volume: _Volume) -> CatalogBook | None:
    """Map an API volume to a CatalogBook, or None (logged at debug level) if unusable."""
    info = volume.volume_info
    isbns = {i.type: i.identifier for i in info.industry_identifiers}
    thumbnail = info.image_links.thumbnail if info.image_links else None
    try:
        return CatalogBook.model_validate(
            {
                "source_id": volume.id,
                "title": info.title or "",
                "authors": info.authors,
                "isbn": isbns.get("ISBN_10"),
                "isbn13": isbns.get("ISBN_13"),
                "pages": info.page_count or None,  # 0 means unknown
                "language": info.language,
                "categories": info.categories,
                "description": _html_to_text(info.description),
                "year": _extract_year(info.published_date),
                "cover_url": _force_https(thumbnail),
                "link": info.canonical_volume_link or info.info_link,
            }
        )
    except ValidationError as e:
        # Debug level: common and harmless; shown by the CLI with --verbose.
        logger.debug("Skipping Google Books volume %r: %s", volume.id, describe(e))
        return None


def _extract_year(published_date: str | None) -> int | None:
    """'1965', '1965-08' and '1965-08-01' all give 1965. Anything else gives None."""
    match = _YEAR_PATTERN.match(published_date or "")
    return int(match.group(1)) if match else None


def _force_https(url: str | None) -> str | None:
    if url and url.startswith("http://"):
        return "https://" + url.removeprefix("http://")
    return url


class _TextExtractor(HTMLParser):
    """Collects the text of an HTML fragment, turning block tags into line breaks."""

    _BREAKS = frozenset({"br", "p", "div", "li", "ul", "ol"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._BREAKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._BREAKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _html_to_text(html: str | None) -> str | None:
    """Strip tags and entities, keep paragraph breaks, collapse whitespace."""
    if not html:
        return None
    extractor = _TextExtractor()
    extractor.feed(html)
    extractor.close()
    text = unescape("".join(extractor.parts))
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line) or None
