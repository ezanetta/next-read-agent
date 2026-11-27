-- Persistent cover jobs (R-001): pending lookups survive restarts and scale to zero.
-- A claimed job is leased; if the process dies, the lease expires and it's claimed again.
CREATE TABLE IF NOT EXISTS cover_jobs (
    id INTEGER PRIMARY KEY,
    profile_id TEXT NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
    book_index INTEGER NOT NULL,
    priority INTEGER NOT NULL,  -- lower first: read, currently reading, to-read
    attempts INTEGER NOT NULL DEFAULT 0,  -- claims so far, crashes included
    due_at TEXT NOT NULL,  -- UTC ISO text; a failed lookup is retried later
    leased_until TEXT,  -- claimed until then
    UNIQUE (profile_id, book_index)
);
CREATE INDEX IF NOT EXISTS cover_jobs_next ON cover_jobs (priority, due_at, id);
