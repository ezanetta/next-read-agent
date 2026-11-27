-- Persistent cover jobs (R-001), the same as sqlite/0003_cover_jobs.sql.
CREATE TABLE IF NOT EXISTS cover_jobs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    profile_id TEXT NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
    book_index INTEGER NOT NULL,
    priority INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    due_at TIMESTAMPTZ NOT NULL,
    leased_until TIMESTAMPTZ,
    UNIQUE (profile_id, book_index)
);
CREATE INDEX IF NOT EXISTS cover_jobs_next ON cover_jobs (priority, due_at, id);
