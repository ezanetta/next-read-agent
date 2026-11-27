-- Covers in their own table (R-008): the cover worker adds one row per cover instead of
-- rewriting the whole profile, and deleting a profile deletes its covers.
CREATE TABLE IF NOT EXISTS profile_covers (
    profile_id TEXT NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
    book_index INTEGER NOT NULL,
    url TEXT NOT NULL,
    PRIMARY KEY (profile_id, book_index)
);
-- Covers saved before this version live in each profile's body: move them.
INSERT OR IGNORE INTO profile_covers (profile_id, book_index, url)
    SELECT p.id, CAST(c.key AS INTEGER), c.value
    FROM profiles AS p, json_each(p.body, '$.covers') AS c;
UPDATE profiles SET body = json_remove(body, '$.covers');
