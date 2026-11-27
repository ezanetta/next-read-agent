-- The demo's stores (D-043), the same tables and columns as sqlite/0001_initial.sql.
CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY,
    body JSONB NOT NULL,
    expires_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS profiles_expires_at ON profiles (expires_at);
CREATE TABLE IF NOT EXISTS search_cache (
    query TEXT PRIMARY KEY,
    body JSONB NOT NULL,
    cached_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS cover_lookups (
    key TEXT PRIMARY KEY,
    url TEXT,
    checked_at TIMESTAMPTZ NOT NULL
);
