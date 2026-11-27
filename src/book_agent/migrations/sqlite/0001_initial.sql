-- The demo's stores (D-043). IF NOT EXISTS: databases made before migrations existed
-- already have these tables, and adopt this version as they are.
CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY,
    body TEXT NOT NULL,
    expires_at TEXT  -- UTC ISO text, so comparing text compares instants
);
CREATE INDEX IF NOT EXISTS profiles_expires_at ON profiles (expires_at);
CREATE TABLE IF NOT EXISTS search_cache (
    query TEXT PRIMARY KEY,
    body TEXT NOT NULL,
    cached_at TEXT NOT NULL
);
-- cover_cache held work covers by title and author, some from translations (F-023).
DROP TABLE IF EXISTS cover_cache;
CREATE TABLE IF NOT EXISTS cover_lookups (
    key TEXT PRIMARY KEY,
    url TEXT,
    checked_at TEXT NOT NULL
);
