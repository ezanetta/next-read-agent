import json
from pathlib import Path

import pytest

from book_agent.adapters.sqlite_store import SqliteStore
from book_agent.cli import EXIT_OK, export_covers, main
from book_agent.covers import Pacer
from book_agent.demo_profiles import load_presets
from book_agent.domain.covers import cover_id_url
from tests.api_support import NOW
from tests.fakes import FakeCoverFinder, FakeTime
from tests.library_data import PROFILE

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


async def test_export_covers_resolves_and_reports_and_a_rerun_uses_the_cache(
    capsys: pytest.CaptureFixture[str],
) -> None:
    time = FakeTime()
    titles = [b.title for b in PROFILE.books]
    finder = FakeCoverFinder({titles[0]: 1, titles[1]: 2}, time)
    store = SqliteStore(":memory:")

    report = await export_covers(
        PROFILE, finder, store, pacer=Pacer(sleep=time.sleep, monotonic=time.monotonic)
    )

    assert report.covers[0] == cover_id_url(1) and report.covers[1] == cover_id_url(2)
    assert report.looked_up == len(PROFILE.books)
    out = capsys.readouterr().out
    assert f"Covers: 2 of {len(PROFILE.books)} books" in out
    calls = len(finder.calls)
    again = await export_covers(PROFILE, finder, store, pacer=Pacer(sleep=time.sleep))
    assert again.covers == report.covers and len(finder.calls) == calls  # all cached
