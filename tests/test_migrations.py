"""Numbered SQL migrations (D-049): both dialects in step, applied once, in order."""

import sqlite3
from pathlib import Path

from book_agent.adapters.sqlite_store import SqliteStore
from book_agent.migrations import apply_sqlite, migrations


def sqlite_columns(db: sqlite3.Connection) -> dict[str, list[str]]:
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    return {t: sorted(r[1] for r in db.execute(f"PRAGMA table_info({t})")) for t in tables}


def test_both_dialects_have_the_same_versions_and_names() -> None:
    sqlite = [(m.version, m.name) for m in migrations("sqlite")]
    postgres = [(m.version, m.name) for m in migrations("postgres")]
    assert sqlite == postgres
    assert [v for v, _ in sqlite] == list(range(1, len(sqlite) + 1))  # no gaps


def test_migrations_apply_once() -> None:
    db = sqlite3.connect(":memory:")
    versions = [m.version for m in migrations("sqlite")]
    assert apply_sqlite(db) == versions
    assert apply_sqlite(db) == []
    recorded = [r[0] for r in db.execute("SELECT version FROM schema_migrations ORDER BY 1")]
    assert recorded == versions


def test_a_database_made_before_migrations_keeps_its_data(tmp_path: Path) -> None:
    path = tmp_path / "demo.sqlite3"
    legacy = sqlite3.connect(path)
    legacy.executescript(
        """
        CREATE TABLE profiles (id TEXT PRIMARY KEY, body TEXT NOT NULL, expires_at TEXT);
        CREATE TABLE search_cache (query TEXT PRIMARY KEY, body TEXT NOT NULL,
                                   cached_at TEXT NOT NULL);
        CREATE TABLE cover_cache (key TEXT PRIMARY KEY, cover_id INTEGER);
        INSERT INTO search_cache VALUES ('dune', '[]', '2026-11-27T12:00:00+00:00');
        """
    )
    legacy.commit()
    legacy.close()

    store = SqliteStore(path)  # applies the migrations on open
    store.close()
    db = sqlite3.connect(path)
    assert db.execute("SELECT query FROM search_cache").fetchall() == [("dune",)]
    assert "cover_cache" not in sqlite_columns(db)  # the wrong-edition cache (F-023)
    assert db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone() == (
        len(migrations("sqlite")),
    )
