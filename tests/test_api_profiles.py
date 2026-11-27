import asyncio
from datetime import timedelta
from pathlib import Path

from book_agent.adapters.sqlite_store import SqliteStore
from book_agent.demo_profiles import PresetFile, load_presets
from book_agent.domain.covers import CachedCover, cover_id_url, cover_key
from book_agent.domain.models import Book, ReadingStatus
from tests.api_support import NOW, client, services
from tests.fakes import FakeCoverFinder
from tests.library_data import PROFILE


def pick(i: int) -> dict[str, object]:
    return {"source_id": f"id{i}", "title": f"Book {i}", "authors": [f"Author {i}"],
            "isbn13": "9780000000002" if i == 0 else None,
            "cover_url": "https://img/1.jpg" if i == 1 else None}  # fmt: skip


FIVE = {"books": [pick(i) for i in range(5)]}


async def test_a_manual_profile_is_created_and_shown_as_picked() -> None:
    api = services()
    async with client(api) as c:
        created = await c.post("/profiles/manual", json={"books": [pick(i) for i in range(5)]})
        assert created.status_code == 201
        summary = created.json()
        assert (summary["source"], summary["books"], summary["liked"]) == ("manual", 5, 5)
        assert summary["ratings_implied"] is True
        assert summary["expires_at"].startswith((NOW + timedelta(days=3)).date().isoformat())
        books = (await c.get(f"/profiles/{summary['id']}/books")).json()["books"]
    assert all(b["picked"] and b["rating"] is None and b["shelf"] == "read" for b in books)
    assert books[0]["cover_url"] is None  # never a cover URL by ISBN (F-023)
    assert books[1]["cover_url"] == "https://img/1.jpg"


async def test_too_few_picks_are_a_validation_error() -> None:
    async with client(services()) as c:
        response = await c.post("/profiles/manual", json={"books": [pick(i) for i in range(4)]})
    assert response.status_code == 422


async def test_duplicate_picks_that_leave_too_few_are_an_invalid_profile() -> None:
    async with client(services()) as c:
        response = await c.post("/profiles/manual", json={"books": [pick(1)] * 5})
    assert response.status_code == 400
    message = "pick between 5 and 50 different books (got 1)"
    assert response.json() == {"error": "invalid_profile", "message": message}


def upload(read_books: int) -> bytes:
    header = "Book Id,Title,Author,My Rating,Exclusive Shelf"
    rows = [f"{i},Book {i},Author {i},4,read" for i in range(read_books)]
    return "\n".join([header, *rows, "99,Later,Someone,0,to-read"]).encode()


async def test_a_goodreads_upload_makes_a_profile_with_real_ratings() -> None:
    async with client(services()) as c:
        created = await c.post("/profiles/goodreads", files={"file": ("export.csv", upload(5))})
        assert created.status_code == 201
        profile_id = created.json()["id"]
        to_read = (await c.get(f"/profiles/{profile_id}/books", params={"shelf": "to_read"})).json()
        read = (await c.get(f"/profiles/{profile_id}/books", params={"shelf": "read"})).json()
    assert [b["title"] for b in to_read["books"]] == ["Later"]
    assert {b["rating"] for b in read["books"]} == {4.0} and not read["books"][0]["picked"]


async def test_a_bad_upload_says_why() -> None:
    async with client(services()) as c:
        response = await c.post("/profiles/goodreads", files={"file": ("x.csv", upload(4))})
    assert response.status_code == 400 and "at least 5 read books" in response.json()["message"]


async def test_presets_are_listed_found_and_cannot_be_deleted(tmp_path: Path) -> None:
    folder = tmp_path / "profiles"
    folder.mkdir()
    (folder / "sample-classics.json").write_text(
        PresetFile(description="Classics", profile=PROFILE).model_dump_json()
    )
    api = services(presets=load_presets(folder, None, NOW))
    async with client(api) as c:
        presets = (await c.get("/profiles/presets")).json()
        assert [p["id"] for p in presets] == ["sample-classics"]
        assert (await c.get("/profiles/sample-classics")).json()["description"] == "Classics"
        deleted = await c.delete("/profiles/sample-classics")
    assert deleted.status_code == 400 and deleted.json()["error"] == "preset"


async def test_unknown_expired_and_deleted_profiles_are_404() -> None:
    api = services()
    async with client(api) as c:
        missing = (await c.get("/profiles/nope")).json()
        assert missing == {"error": "not_found", "message": "profile not found"}
        created = (await c.post("/profiles/manual", json=FIVE)).json()
        assert (await c.delete(f"/profiles/{created['id']}")).status_code == 204
        assert (await c.get(f"/profiles/{created['id']}")).status_code == 404
        created = (await c.post("/profiles/manual", json=FIVE)).json()
        api.clock = lambda: NOW + timedelta(days=4)
        assert (await c.get(f"/profiles/{created['id']}/books")).status_code == 404


async def test_summaries_list_top_authors_and_covers() -> None:
    books = [
        {**pick(i), "authors": ["Ishiguro" if i < 3 else f"Author {i}"],
         "cover_url": f"https://img/{i}.jpg" if i % 2 else None}
        for i in range(6)
    ]  # fmt: skip
    async with client(services()) as c:
        summary = (await c.post("/profiles/manual", json={"books": books})).json()
    assert summary["top_authors"][0] == "Ishiguro" and len(summary["top_authors"]) == 3
    assert summary["cover_urls"] == ["https://img/1.jpg", "https://img/3.jpg", "https://img/5.jpg"]


async def test_an_upload_returns_at_once_and_its_covers_arrive_by_cover_id() -> None:
    gate = asyncio.Event()  # lookups wait until the first page has been checked
    finder = FakeCoverFinder({"Book 0": 100, "Book 1": None, "Later": 300}, gate=gate)
    api = services(covers=finder)
    assert api.covers is not None
    # Another profile already resolved Book 2: it's known before any request.
    store = api.profiles
    assert isinstance(store, SqliteStore)
    await store.put_cover(
        cover_key(Book(title="Book 2", author="Author 2", status=ReadingStatus.READ)),
        CachedCover(cover_id_url(200), NOW),
    )
    async with client(api) as c:
        created = await c.post("/profiles/goodreads", files={"file": ("export.csv", upload(5))})
        profile_id = created.json()["id"]
        first = (await c.get(f"/profiles/{profile_id}/books")).json()
        gate.set()
        await api.covers.join()
        done = (await c.get(f"/profiles/{profile_id}/books")).json()
        await api.covers.aclose()

    assert created.status_code == 201
    covers_now = {b["title"]: b["cover_url"] for b in first["books"]}
    assert covers_now["Book 2"] == cover_id_url(200) and covers_now["Book 0"] is None
    assert first["covers_pending"] == 5  # Book 0, 1, 3, 4 and Later: everything but Book 2
    assert done["covers_pending"] == 0
    covers = {b["title"]: b["cover_url"] for b in done["books"]}
    assert covers == {
        "Book 0": cover_id_url(100), "Book 1": None, "Book 2": cover_id_url(200),
        "Book 3": None, "Book 4": None, "Later": cover_id_url(300),
    }  # fmt: skip
    assert all("isbn" not in (url or "") for url in covers.values())
    assert finder.searched[-1] == "Later"  # the to-read shelf goes last


async def test_without_a_cover_queue_nothing_is_pending() -> None:
    async with client(services()) as c:
        created = await c.post("/profiles/goodreads", files={"file": ("export.csv", upload(5))})
        page = (await c.get(f"/profiles/{created.json()['id']}/books")).json()
    assert page["covers_pending"] == 0
