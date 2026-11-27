-- Covers in their own table (R-008), the same as sqlite/0002_profile_covers.sql.
CREATE TABLE IF NOT EXISTS profile_covers (
    profile_id TEXT NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
    book_index INTEGER NOT NULL,
    url TEXT NOT NULL,
    PRIMARY KEY (profile_id, book_index)
);
INSERT INTO profile_covers (profile_id, book_index, url)
    SELECT p.id, c.key::INTEGER, c.value
    FROM profiles AS p, jsonb_each_text(COALESCE(p.body -> 'covers', '{}'::jsonb)) AS c
    ON CONFLICT DO NOTHING;
UPDATE profiles SET body = body - 'covers';
