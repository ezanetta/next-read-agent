"""Running tests against a real Postgres, when one is available (D-048).

Set POSTGRES_TEST_URL (the docker-compose service, see the README) to run the Postgres
cases; without it they're skipped with this reason. Each test gets its own schema,
dropped afterwards, so tests never see each other's rows or the development data.
"""

import os
import secrets
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

POSTGRES_TEST_URL = os.environ.get("POSTGRES_TEST_URL")
SKIP_REASON = (
    "POSTGRES_TEST_URL is not set: start Postgres with `docker compose up -d postgres` "
    "and set it to run the Postgres cases (README, 'Postgres contract tests')"
)
needs_postgres = pytest.mark.skipif(POSTGRES_TEST_URL is None, reason=SKIP_REASON)


@asynccontextmanager
async def fresh_schema() -> AsyncGenerator[str]:
    """A connection string whose search_path is a new, empty schema."""
    assert POSTGRES_TEST_URL is not None
    schema = f"test_{secrets.token_hex(6)}"
    async with await psycopg.AsyncConnection.connect(POSTGRES_TEST_URL, autocommit=True) as admin:
        await admin.execute(f'CREATE SCHEMA "{schema}"'.encode())
        try:
            yield make_conninfo(POSTGRES_TEST_URL, options=f"-c search_path={schema}")
        finally:
            await admin.execute(f'DROP SCHEMA "{schema}" CASCADE'.encode())
