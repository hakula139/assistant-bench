CREATE TABLE actors (
    id TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    owner_namespace TEXT UNIQUE
) STRICT;

CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL REFERENCES actors(id),
    family TEXT NOT NULL,
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    import_source TEXT REFERENCES import_sources(id)
) STRICT;

CREATE TABLE session_state (
    session_id TEXT PRIMARY KEY REFERENCES sessions(id),
    version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
    current_lease TEXT
) STRICT;

CREATE TABLE actor_resolution (
    family TEXT NOT NULL,
    actor_id TEXT NOT NULL REFERENCES actors(id),
    canonical_actor TEXT NOT NULL REFERENCES actors(id),
    event_seq INTEGER,
    PRIMARY KEY (family, actor_id)
) STRICT;

CREATE INDEX actor_resolution_canonical ON actor_resolution(family, canonical_actor);

CREATE TRIGGER sessions_initialize AFTER INSERT ON sessions
BEGIN
    INSERT INTO session_state(session_id) VALUES (NEW.id);
    INSERT INTO actor_resolution(family, actor_id, canonical_actor)
    SELECT NEW.family, NEW.actor_id, NEW.actor_id
    WHERE NOT EXISTS (
        SELECT 1 FROM actor_resolution
        WHERE family = NEW.family AND actor_id = NEW.actor_id
    );
END;

CREATE TABLE evidence_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    command_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('activate', 'accept', 'withdraw', 'exclude', 'restore', 'attribute', 'import')),
    family TEXT NOT NULL,
    principal TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    data TEXT NOT NULL CHECK (json_valid(data))
) STRICT;

CREATE INDEX evidence_events_family ON evidence_events(family, seq);

CREATE TABLE claims (
    id TEXT PRIMARY KEY,
    family TEXT NOT NULL,
    canonical_actor TEXT NOT NULL REFERENCES actors(id),
    kind TEXT NOT NULL CHECK (kind IN ('ordinary', 'repeat')),
    comparison_key TEXT NOT NULL,
    repeat_of INTEGER,
    version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
    selected_receipt INTEGER,
    current_lease TEXT,
    merged_into TEXT REFERENCES claims(id),
    CHECK ((kind = 'repeat') = (repeat_of IS NOT NULL))
) STRICT;

CREATE UNIQUE INDEX claims_current ON claims(family, canonical_actor, kind, comparison_key)
WHERE merged_into IS NULL;

CREATE INDEX claims_selection ON claims(selected_receipt);

CREATE TABLE pair_leases (
    id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL,
    actor_id TEXT NOT NULL REFERENCES actors(id),
    family TEXT NOT NULL,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    claim_id TEXT NOT NULL REFERENCES claims(id),
    claim_version INTEGER NOT NULL CHECK (claim_version >= 0),
    evaluation_id TEXT NOT NULL REFERENCES evaluations(id),
    publication_id TEXT NOT NULL REFERENCES publications(id),
    point_id TEXT NOT NULL REFERENCES point_snapshots(id),
    case_key TEXT NOT NULL,
    a_output TEXT NOT NULL,
    b_output TEXT NOT NULL,
    a_system TEXT NOT NULL,
    b_system TEXT NOT NULL,
    a_fingerprint TEXT NOT NULL,
    b_fingerprint TEXT NOT NULL,
    issued_at INTEGER NOT NULL CHECK (issued_at >= 0),
    expires_at INTEGER NOT NULL CHECK (expires_at > issued_at),
    repeat_of INTEGER,
    status TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'revoked')),
    receipt_seq INTEGER,
    CHECK (a_system != b_system AND a_output != b_output)
) STRICT;

CREATE UNIQUE INDEX leases_actor_pending ON pair_leases(actor_id, family) WHERE status = 'pending';
CREATE UNIQUE INDEX leases_claim_pending ON pair_leases(claim_id) WHERE status = 'pending';
CREATE INDEX leases_repeat ON pair_leases(repeat_of) WHERE status = 'pending';

CREATE TABLE vote_receipts (
    seq INTEGER PRIMARY KEY REFERENCES evidence_events(seq),
    acknowledgment TEXT NOT NULL UNIQUE,
    lease_id TEXT NOT NULL UNIQUE,
    actor_id TEXT NOT NULL REFERENCES actors(id),
    session_id TEXT NOT NULL REFERENCES sessions(id),
    family TEXT NOT NULL,
    evaluation_id TEXT NOT NULL REFERENCES evaluations(id),
    publication_id TEXT NOT NULL REFERENCES publications(id),
    point_id TEXT NOT NULL REFERENCES point_snapshots(id),
    claim_id TEXT NOT NULL REFERENCES claims(id),
    claim_version INTEGER NOT NULL CHECK (claim_version >= 0),
    case_key TEXT NOT NULL,
    a_output TEXT NOT NULL,
    b_output TEXT NOT NULL,
    a_system TEXT NOT NULL,
    b_system TEXT NOT NULL,
    a_fingerprint TEXT NOT NULL,
    b_fingerprint TEXT NOT NULL,
    choice TEXT NOT NULL CHECK (choice IN ('a', 'b', 'tie', 'both_bad')),
    defects TEXT NOT NULL CHECK (json_valid(defects)),
    duration_ms INTEGER NOT NULL CHECK (duration_ms >= 0),
    repeat_of INTEGER REFERENCES vote_receipts(seq),
    accepted_at INTEGER NOT NULL CHECK (accepted_at >= 0),
    import_source TEXT REFERENCES import_sources(id),
    original_id TEXT
) STRICT;

CREATE INDEX receipts_session ON vote_receipts(session_id, seq DESC);
CREATE INDEX receipts_family ON vote_receipts(family, seq);
CREATE INDEX receipts_actor ON vote_receipts(actor_id, claim_id);

CREATE TABLE receipt_state (
    receipt_seq INTEGER PRIMARY KEY REFERENCES vote_receipts(seq),
    withdrawn INTEGER REFERENCES evidence_events(seq),
    excluded INTEGER REFERENCES evidence_events(seq),
    superseded INTEGER REFERENCES evidence_events(seq)
) STRICT;

CREATE TABLE session_cases (
    session_id TEXT NOT NULL REFERENCES sessions(id),
    case_key TEXT NOT NULL,
    receipt_seq INTEGER NOT NULL REFERENCES vote_receipts(seq),
    PRIMARY KEY (session_id, case_key)
) STRICT;

CREATE TABLE event_targets (
    event_seq INTEGER NOT NULL REFERENCES evidence_events(seq),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    receipt_seq INTEGER REFERENCES vote_receipts(seq),
    claim_id TEXT REFERENCES claims(id),
    expected_version INTEGER CHECK (expected_version >= 0),
    transition TEXT NOT NULL,
    reference_seq INTEGER REFERENCES evidence_events(seq),
    PRIMARY KEY (event_seq, ordinal)
) STRICT;

CREATE INDEX targets_receipt ON event_targets(receipt_seq, event_seq);

CREATE TABLE coverage (
    family TEXT NOT NULL,
    a_system TEXT NOT NULL,
    b_system TEXT NOT NULL,
    count INTEGER NOT NULL CHECK (count >= 0),
    PRIMARY KEY (family, a_system, b_system)
) STRICT;

CREATE TABLE coverage_members (
    receipt_seq INTEGER PRIMARY KEY REFERENCES vote_receipts(seq),
    claim_id TEXT NOT NULL UNIQUE REFERENCES claims(id),
    family TEXT NOT NULL,
    a_system TEXT NOT NULL,
    b_system TEXT NOT NULL
) STRICT;

CREATE TRIGGER coverage_members_insert AFTER INSERT ON coverage_members
BEGIN
    INSERT INTO coverage(family, a_system, b_system, count)
    VALUES (NEW.family, NEW.a_system, NEW.b_system, 1)
    ON CONFLICT(family, a_system, b_system) DO UPDATE SET count = count + 1;
END;

CREATE TRIGGER coverage_members_delete AFTER DELETE ON coverage_members
BEGIN
    UPDATE coverage SET count = count - 1
    WHERE family = OLD.family AND a_system = OLD.a_system AND b_system = OLD.b_system;
END;

CREATE VIEW compatible_leases AS
SELECT l.* FROM pair_leases l
JOIN family_state f ON f.family = l.family
JOIN evaluation_outputs a ON a.evaluation_id = f.evaluation_id AND a.output_key = l.a_output
JOIN evaluation_outputs b ON b.evaluation_id = f.evaluation_id AND b.output_key = l.b_output
JOIN evaluation_systems sa ON sa.evaluation_id = a.evaluation_id AND sa.system_id = a.system_id
JOIN evaluation_systems sb ON sb.evaluation_id = b.evaluation_id AND sb.system_id = b.system_id
WHERE a.case_key = l.case_key AND b.case_key = l.case_key
    AND a.system_id = l.a_system AND b.system_id = l.b_system
    AND sa.fingerprint = l.a_fingerprint AND sb.fingerprint = l.b_fingerprint
    AND a.verdict = 'pass' AND b.verdict = 'pass' AND a.content_digest != b.content_digest;

CREATE VIEW eligible_claims AS
SELECT c.id AS claim_id, r.seq AS receipt_seq, r.family, r.a_system, r.b_system
FROM claims c
JOIN vote_receipts r ON r.seq = c.selected_receipt
JOIN receipt_state s ON s.receipt_seq = r.seq
JOIN family_state f ON f.family = r.family
JOIN evaluation_outputs a ON a.evaluation_id = f.evaluation_id AND a.output_key = r.a_output
JOIN evaluation_outputs b ON b.evaluation_id = f.evaluation_id AND b.output_key = r.b_output
JOIN evaluation_systems sa ON sa.evaluation_id = a.evaluation_id AND sa.system_id = a.system_id
JOIN evaluation_systems sb ON sb.evaluation_id = b.evaluation_id AND sb.system_id = b.system_id
WHERE c.merged_into IS NULL AND c.kind = 'ordinary'
    AND s.withdrawn IS NULL AND s.excluded IS NULL AND s.superseded IS NULL
    AND a.case_key = r.case_key AND b.case_key = r.case_key
    AND a.system_id = r.a_system AND b.system_id = r.b_system
    AND sa.fingerprint = r.a_fingerprint AND sb.fingerprint = r.b_fingerprint
    AND a.verdict = 'pass' AND b.verdict = 'pass' AND a.content_digest != b.content_digest;

CREATE VIEW refresh_coverage AS SELECT NULL AS claim_id WHERE 0;

CREATE TRIGGER refresh_coverage_insert INSTEAD OF INSERT ON refresh_coverage
BEGIN
    DELETE FROM coverage_members WHERE claim_id = NEW.claim_id;
    INSERT INTO coverage_members(receipt_seq, claim_id, family, a_system, b_system)
    SELECT receipt_seq, claim_id, family, a_system, b_system
    FROM eligible_claims WHERE claim_id = NEW.claim_id;
END;

CREATE TRIGGER evidence_events_no_update BEFORE UPDATE ON evidence_events
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER evidence_events_no_delete BEFORE DELETE ON evidence_events
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER evidence_events_no_replace BEFORE INSERT ON evidence_events
WHEN EXISTS (SELECT 1 FROM evidence_events WHERE seq = NEW.seq OR command_id = NEW.command_id)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER vote_receipts_no_update BEFORE UPDATE ON vote_receipts
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER vote_receipts_no_delete BEFORE DELETE ON vote_receipts
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER vote_receipts_no_replace BEFORE INSERT ON vote_receipts
WHEN EXISTS (SELECT 1 FROM vote_receipts WHERE seq = NEW.seq OR acknowledgment = NEW.acknowledgment OR lease_id = NEW.lease_id)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER actors_no_update BEFORE UPDATE ON actors
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER actors_no_delete BEFORE DELETE ON actors
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER actors_no_replace BEFORE INSERT ON actors
WHEN EXISTS (SELECT 1 FROM actors WHERE id = NEW.id OR owner_namespace = NEW.owner_namespace)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER sessions_no_update BEFORE UPDATE ON sessions
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER sessions_no_delete BEFORE DELETE ON sessions
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER sessions_no_replace BEFORE INSERT ON sessions
WHEN EXISTS (SELECT 1 FROM sessions WHERE id = NEW.id)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER event_targets_no_update BEFORE UPDATE ON event_targets
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER event_targets_no_delete BEFORE DELETE ON event_targets
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER event_targets_no_replace BEFORE INSERT ON event_targets
WHEN EXISTS (SELECT 1 FROM event_targets WHERE event_seq = NEW.event_seq AND ordinal = NEW.ordinal)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
