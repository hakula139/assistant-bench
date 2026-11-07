ALTER TABLE publications ADD COLUMN render_new TEXT NOT NULL DEFAULT 'text'
  CHECK (render_new IN ('text', 'diff', 'html'));
UPDATE publications SET render_new = render;
ALTER TABLE publications DROP COLUMN render;
ALTER TABLE publications RENAME COLUMN render_new TO render;

ALTER TABLE outputs ADD COLUMN length_new INTEGER;
UPDATE outputs SET length_new = length;
ALTER TABLE outputs DROP COLUMN length;
ALTER TABLE outputs RENAME COLUMN length_new TO length;
