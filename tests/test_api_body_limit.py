"""R-005: request bodies over their limit are rejected with 413 while they arrive."""

from collections.abc import AsyncGenerator

from book_agent.adapters.sqlite_store import SqliteStore
from book_agent.api.app import BODY_LIMITS
from book_agent.api.body_limit import MAX_JSON_BODY
from book_agent.demo_profiles import CSV_MAX_BYTES
from tests.api_support import NOW, client, services

UPLOAD_LIMIT = BODY_LIMITS["/profiles/goodreads"]
HEADER = b"Book Id,Title,Author,My Rating,Exclusive Shelf\n"


def csv_of(size: int) -> bytes:
    row = b"1,A Title,An Author,4,read\n"
    return HEADER + row * ((size - len(HEADER)) // len(row) + 1)


async def test_an_upload_declared_over_the_limit_is_rejected_before_it_is_read() -> None:
    api = services()
    async with client(api) as c:
        response = await c.post(
            "/profiles/goodreads", files={"file": ("big.csv", csv_of(UPLOAD_LIMIT + 1))}
        )
    assert response.status_code == 413
    assert response.json() == {
        "error": "too_large",
        "message": f"the request body is over {UPLOAD_LIMIT // 1024} KB",
    }
    store = api.profiles
    assert isinstance(store, SqliteStore)
    assert await store.purge_expired(NOW.replace(year=2100)) == 0  # no profile was created


async def test_a_chunked_upload_stops_being_read_once_it_passes_the_limit() -> None:
    chunk = b"x" * 64 * 1024
    sent = 0
    total_chunks = (UPLOAD_LIMIT // len(chunk)) * 4  # four times the limit

    async def body() -> AsyncGenerator[bytes]:
        nonlocal sent
        boundary = b'--b\r\nContent-Disposition: form-data; name="file"; filename="x.csv"\r\n\r\n'
        yield boundary
        for _ in range(total_chunks):
            sent += 1
            yield chunk
        yield b"\r\n--b--\r\n"

    async with client(services()) as c:
        response = await c.post(
            "/profiles/goodreads",
            content=body(),  # no Content-Length: sent chunked
            headers={"Content-Type": "multipart/form-data; boundary=b"},
        )
    assert response.status_code == 413 and response.json()["error"] == "too_large"
    assert sent < total_chunks  # rejected early, the rest was never read


async def test_a_json_body_over_64_kb_is_rejected() -> None:
    async with client(services()) as c:
        response = await c.post(
            "/recommendations",
            content=b'{"profile_id": "p", "request": "' + b"x" * MAX_JSON_BODY + b'"}',
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 413


async def test_an_upload_just_under_the_limit_is_still_parsed() -> None:
    async with client(services()) as c:
        response = await c.post(
            "/profiles/goodreads", files={"file": ("big.csv", csv_of(CSV_MAX_BYTES + 10))}
        )
    # Past the body limit's check; the route's own CSV limit answers instead.
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_profile"


async def test_get_requests_are_not_limited() -> None:
    async with client(services()) as c:
        response = await c.get("/health")
    assert response.status_code == 200
