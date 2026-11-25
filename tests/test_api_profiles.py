from datetime import timedelta
from pathlib import Path

from book_agent.demo_profiles import PresetFile, load_presets
from tests.api_support import NOW, client, services
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
    assert books[0]["cover_url"].startswith("https://covers.openlibrary.org/b/isbn/9780000000002")
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
