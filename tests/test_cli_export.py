import json
from pathlib import Path

import pytest
from pydantic import HttpUrl

from book_agent.adapters.sqlite_store import SqliteStore
from book_agent.cli import EXIT_OK, export_covers, main
from book_agent.covers import Pacer
from book_agent.demo_profiles import load_presets
from book_agent.domain.covers import cover_id_url
from tests.api_support import NOW
from tests.fakes import FakeBookSearch, FakeCoverFinder, FakeTime
from tests.library_data import PROFILE, catalog_book

CSV = "Book Id,Title,Author,My Rating,Exclusive Shelf\n" + "\n".join(
    f"{i},Book {i},Author {i},4,read" for i in range(5)
)


def test_no_covers_writes_the_owner_file_without_settings_or_requests(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    csv, out = tmp_path / "export.csv", tmp_path / "owner.json"
    csv.write_text(CSV)
    assert main(["export-profile", "--out", str(out), "--no-covers", "--csv", str(csv)]) == EXIT_OK
    data = json.loads(out.read_text())
    assert data["covers"] == {} and len(data["profile"]["books"]) == 5
    assert "and 0 cover URLs" in capsys.readouterr().out
    assert load_presets(tmp_path / "none", out, NOW)["owner"].covers == {}


async def test_export_covers_uses_open_library_then_google_books_and_a_rerun_is_free(
    capsys: pytest.CaptureFixture[str],
) -> None:
    time = FakeTime()
    # Dune has an ISBN (its edition's cover), Mistborn is found by search, and Twilight
    # only on Google Books.
    dune, mistborn, twilight = PROFILE.books[:3]
    assert dune.isbn13 and not mistborn.isbn13 and not twilight.isbn13
    finder = FakeCoverFinder({mistborn.title: 2}, time, editions={dune.isbn13: 1})
    on_google = catalog_book("g", twilight.title, twilight.author, 300)
    google = FakeBookSearch(
        [on_google.model_copy(update={"cover_url": HttpUrl("https://books.google/g.jpg")})]
    )
    store = SqliteStore(":memory:")

    def pacers() -> dict[str, Pacer]:
        return {
            "pacer": Pacer(sleep=time.sleep, monotonic=time.monotonic),
            "google_pacer": Pacer(0.7, sleep=time.sleep, monotonic=time.monotonic),
        }

    covers = await export_covers(PROFILE, finder, store, google, **pacers())

    assert covers[0] == cover_id_url(1) and covers[1] == cover_id_url(2)
    assert covers[2] == "https://books.google/g.jpg"
    out = capsys.readouterr().out
    assert f"Covers after Open Library: 2 of {len(PROFILE.books)} books" in out
    assert f"Covers after Google Books: 3 of {len(PROFILE.books)} books" in out
    requests = (len(finder.calls), len(google.searches) + len(google.finds))
    again = await export_covers(PROFILE, finder, store, google, **pacers())
    assert again == covers
    assert (len(finder.calls), len(google.searches) + len(google.finds)) == requests
