ALTER TABLE publications ADD COLUMN target TEXT NOT NULL DEFAULT 'production'
    CHECK (target IN ('production', 'private'));

ALTER TABLE cases ADD COLUMN source TEXT
    CHECK (source IN ('public', 'synthetic', 'transcript', 'held-out'));
