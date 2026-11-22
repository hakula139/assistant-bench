CREATE TABLE leaderboard_candidates (
    family TEXT NOT NULL,
    hour_slot INTEGER NOT NULL,
    captured_at INTEGER NOT NULL CHECK (captured_at >= hour_slot),
    digest TEXT NOT NULL,
    cutoffs_json TEXT CHECK (cutoffs_json IS NULL OR json_valid(cutoffs_json)),
    report_json TEXT CHECK (report_json IS NULL OR json_valid(report_json)),
    published_at INTEGER CHECK (published_at >= captured_at),
    PRIMARY KEY (family, hour_slot)
) STRICT;

CREATE INDEX leaderboard_published ON leaderboard_candidates (family, hour_slot)
WHERE published_at IS NOT NULL;
