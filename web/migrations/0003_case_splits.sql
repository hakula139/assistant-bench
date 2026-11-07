ALTER TABLE cases ADD COLUMN split TEXT NOT NULL DEFAULT 'public'
    CHECK (split IN ('public', 'held-out', 'private'));
