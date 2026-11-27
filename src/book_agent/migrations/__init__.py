"""Schema migrations: numbered SQL files per dialect, applied in order (D-049).

`sqlite/NNNN_name.sql` and `postgres/NNNN_name.sql` describe the same change; a test
checks that both dialects have the same versions and end with the same tables and
columns. Applied versions are recorded in `schema_migrations`, so each file runs once.
"""

import re
import sqlite3
from dataclasses import dataclass
from importlib.resources import files
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from psycopg import AsyncConnection
    from psycopg.rows import TupleRow

Dialect = Literal["sqlite", "postgres"]
_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


def migrations(dialect: Dialect) -> list[Migration]:
    """Every migration for `dialect`, in version order. Raises ValueError for a file that
    isn't named NNNN_name.sql, or for a repeated version."""
    found: list[Migration] = []
    for entry in files(__name__).joinpath(dialect).iterdir():
        if not entry.name.endswith(".sql"):
            continue
        match = _NAME.match(entry.name)
        if match is None:
            raise ValueError(f"migration file {entry.name!r} isn't named NNNN_name.sql")
        found.append(Migration(int(match[1]), match[2], entry.read_text(encoding="utf-8")))
    found.sort(key=lambda m: m.version)
    versions = [m.version for m in found]
    if len(set(versions)) != len(versions):
        raise ValueError(f"repeated migration version in {dialect}: {versions}")
    return found


SCHEMA_MIGRATIONS = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL
)
"""
# Any constant works: it only has to be the same for every process migrating this database.
MIGRATION_LOCK = 4_046_049


def apply_sqlite(db: sqlite3.Connection) -> list[int]:
    """Apply the SQLite migrations not applied yet, each in its own transaction with its
    version record. Returns the versions applied now."""
    db.execute(SCHEMA_MIGRATIONS)
    db.commit()
    done: set[int] = {int(row[0]) for row in db.execute("SELECT version FROM schema_migrations")}
    applied: list[int] = []
    for migration in migrations("sqlite"):
        if migration.version in done:
            continue
        db.executescript(
            f"BEGIN;\n{migration.sql}\n"
            f"INSERT INTO schema_migrations (version, name) "
            f"VALUES ({migration.version}, '{migration.name}');\nCOMMIT;"
        )
        applied.append(migration.version)
    return applied


async def apply_postgres(connection: "AsyncConnection[TupleRow]") -> list[int]:
    """Apply the Postgres migrations not applied yet, in one transaction holding an
    advisory lock. Two processes starting together (a revision switch): the second waits
    on the lock, and once the first commits it reads the recorded versions afresh (READ
    COMMITTED) and applies nothing. The lock is transaction-scoped: released at commit,
    rollback or a dropped connection, so a crash can't leave it held."""
    applied: list[int] = []
    async with connection.transaction():
        await connection.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK,))
        await connection.execute(SCHEMA_MIGRATIONS)
        cursor = await connection.execute("SELECT version FROM schema_migrations")
        done: set[int] = {int(row[0]) for row in await cursor.fetchall()}
        for migration in migrations("postgres"):
            if migration.version in done:
                continue
            await connection.execute(migration.sql.encode())
            await connection.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (%s, %s)",
                (migration.version, migration.name),
            )
            applied.append(migration.version)
    return applied
