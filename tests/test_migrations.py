"""Numbered SQL migrations (D-049): both dialects in step, applied once, in order."""

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from book_agent.adapters.postgres_store import PostgresStore
from book_agent.adapters.sqlite_store import SqliteStore
from book_agent.domain.profiles import StoredProfile
from book_agent.migrations import apply_postgres, apply_sqlite, migrations
from tests.library_data import PROFILE
from tests.postgres_support import fresh_schema, needs_postgres

NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)


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


@needs_postgres
async def test_postgres_ends_with_the_same_tables_and_columns_as_sqlite() -> None:
    db = sqlite3.connect(":memory:")
    apply_sqlite(db)
    async with (
        fresh_schema() as conninfo,
        PostgresStore.open(conninfo),  # applies the migrations
        await psycopg.AsyncConnection.connect(conninfo) as connection,
    ):
        cursor = await connection.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema()"
        )
        postgres: dict[str, list[str]] = {}
        for table, column in await cursor.fetchall():
            postgres.setdefault(table, []).append(column)
        again = await apply_postgres(connection)  # already applied: nothing to do
    assert {t: sorted(c) for t, c in postgres.items()} == sqlite_columns(db)
    assert again == []


def test_covers_saved_inside_profile_bodies_move_to_their_own_table(tmp_path: Path) -> None:
    path = tmp_path / "demo.sqlite3"
    legacy = sqlite3.connect(path)
    legacy.execute(
        "CREATE TABLE profiles (id TEXT PRIMARY KEY, body TEXT NOT NULL, expires_at TEXT)"
    )
    body = StoredProfile(
        id="p", source="manual", profile=PROFILE, covers={0: "https://c/0", 2: "https://c/2"},
        created_at=NOW,
    ).model_dump_json()  # fmt: skip
    legacy.execute("INSERT INTO profiles VALUES ('p', ?, NULL)", (body,))
    legacy.commit()
    legacy.close()

    store = SqliteStore(path)
    stored = asyncio.run(store.get("p", NOW))
    store.close()
    assert stored is not None and stored.covers == {0: "https://c/0", 2: "https://c/2"}
    db = sqlite3.connect(path)
    assert "covers" not in json.loads(db.execute("SELECT body FROM profiles").fetchone()[0])


@needs_postgres
async def test_postgres_moves_covers_out_of_profile_bodies() -> None:
    body = StoredProfile(
        id="p", source="manual", profile=PROFILE, covers={0: "https://c/0"}, created_at=NOW
    ).model_dump(mode="json")
    move = next(m for m in migrations("postgres") if m.name == "profile_covers")
    async with (
        fresh_schema() as conninfo,
        PostgresStore.open(conninfo) as store,
        await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as connection,
    ):
        # A row as version 1 stored it, covers inside the body; then version 2 again.
        await connection.execute("INSERT INTO profiles (id, body) VALUES ('p', %s)", (Jsonb(body),))
        await connection.execute(move.sql.encode())
        stored = await store.get("p", NOW)
        cursor = await connection.execute("SELECT body ? 'covers' FROM profiles")
        still_inside = await cursor.fetchone()
    assert stored is not None and stored.covers == {0: "https://c/0"}
    assert still_inside == (False,)
