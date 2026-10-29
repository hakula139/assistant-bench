CREATE TABLE publications (
    id TEXT PRIMARY KEY,
    family TEXT NOT NULL,
    display_name TEXT NOT NULL,
    aspect TEXT,
    render TEXT NOT NULL CHECK (render IN ('text', 'diff')),
    defects TEXT NOT NULL CHECK (json_valid(defects)),
    systems TEXT NOT NULL CHECK (json_valid(systems)),
    case_count INTEGER NOT NULL,
    output_count INTEGER NOT NULL,
    created_at INTEGER NOT NULL
) STRICT;

CREATE TABLE cases (
    publication_id TEXT NOT NULL REFERENCES publications (id),
    case_key TEXT NOT NULL,
    category TEXT NOT NULL,
    instruction TEXT NOT NULL,
    checklist TEXT NOT NULL CHECK (json_valid(checklist)),
    PRIMARY KEY (publication_id, case_key)
) STRICT;

CREATE TABLE outputs (
    publication_id TEXT NOT NULL,
    output_key TEXT NOT NULL,
    case_key TEXT NOT NULL,
    system TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('pass', 'fail', 'ungraded')),
    errored INTEGER NOT NULL CHECK (errored IN (0, 1)),
    length INTEGER NOT NULL,
    content_digest TEXT NOT NULL,
    content TEXT,
    CHECK ((verdict = 'pass') = (content IS NOT NULL)),
    PRIMARY KEY (publication_id, output_key),
    FOREIGN KEY (publication_id, case_key) REFERENCES cases (publication_id, case_key)
) STRICT;

CREATE TABLE active (
    family TEXT PRIMARY KEY,
    publication_id TEXT NOT NULL REFERENCES publications (id),
    activated_at INTEGER NOT NULL
) STRICT;

CREATE TABLE actors (
    id TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    ip_tag TEXT
) STRICT;

CREATE TABLE pairs (
    id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL REFERENCES actors (id),
    family TEXT NOT NULL,
    publication_id TEXT NOT NULL,
    case_key TEXT NOT NULL,
    a_output TEXT NOT NULL,
    b_output TEXT NOT NULL,
    issued_at INTEGER NOT NULL,
    UNIQUE (actor_id, family),
    FOREIGN KEY (publication_id, a_output) REFERENCES outputs (publication_id, output_key),
    FOREIGN KEY (publication_id, b_output) REFERENCES outputs (publication_id, output_key)
) STRICT;

CREATE TABLE votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_id TEXT NOT NULL UNIQUE,
    actor_id TEXT NOT NULL REFERENCES actors (id),
    family TEXT NOT NULL,
    publication_id TEXT NOT NULL,
    case_key TEXT NOT NULL,
    a_output TEXT NOT NULL,
    b_output TEXT NOT NULL,
    a_system TEXT NOT NULL,
    b_system TEXT NOT NULL,
    choice TEXT NOT NULL CHECK (choice IN ('a', 'b', 'tie', 'both_bad')),
    defects TEXT NOT NULL CHECK (json_valid(defects)),
    elapsed_ms INTEGER NOT NULL CHECK (elapsed_ms >= 0),
    recorded_at INTEGER NOT NULL,
    ip_tag TEXT,
    exclusion TEXT,
    FOREIGN KEY (publication_id, a_output) REFERENCES outputs (publication_id, output_key),
    FOREIGN KEY (publication_id, b_output) REFERENCES outputs (publication_id, output_key)
) STRICT;

CREATE UNIQUE INDEX votes_comparison
    ON votes (actor_id, family, case_key, min(a_system, b_system), max(a_system, b_system));
CREATE INDEX votes_family ON votes (family, id);
CREATE INDEX votes_ip ON votes (ip_tag, recorded_at);
CREATE INDEX actors_ip ON actors (ip_tag, created_at);
