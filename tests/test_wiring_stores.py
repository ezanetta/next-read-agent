"""open_stores: DATABASE_URL picks Postgres, otherwise SQLite; both close on exit."""

from datetime import UTC, datetime
from pathlib import Path

from pydantic import SecretStr

from book_agent.adapters.postgres_store import PostgresStore
from book_agent.adapters.sqlite_store import SqliteStore
from book_agent.config import Settings
from book_agent.wiring import open_stores
from tests.postgres_support import fresh_schema, needs_postgres


def settings(**values: object) -> Settings:
    return Settings.model_validate({"gemini_api_key": "test-key", **values})


async def test_without_a_database_url_the_stores_are_sqlite(tmp_path: Path) -> None:
    path = tmp_path / "demo.sqlite3"
    async with open_stores(settings(demo_db_path=path)) as stores:
        assert isinstance(stores.profiles, SqliteStore) and stores.covers is stores.profiles
        assert await stores.profiles.purge_expired(datetime.now(UTC)) == 0
    assert path.exists()


@needs_postgres
async def test_a_database_url_picks_postgres_and_the_pool_closes_on_exit() -> None:
    async with fresh_schema() as conninfo:
        config = settings(database_url=SecretStr(conninfo), db_pool_max_size=2)
        async with open_stores(config) as stores:
            store = stores.profiles
            assert isinstance(store, PostgresStore) and stores.covers is store
            pool = store._pool  # pyright: ignore[reportPrivateUsage]
            assert pool.max_size == 2 and not pool.closed
        assert pool.closed
