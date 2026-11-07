CREATE TABLE publication_preparations (
    publication_id TEXT PRIMARY KEY REFERENCES publications (id),
    target TEXT NOT NULL CHECK (target IN ('production', 'private')),
    collection_id TEXT NOT NULL REFERENCES collections (id),
    case_revision TEXT,
    prepared_at TEXT NOT NULL
) STRICT;

CREATE TRIGGER preparations_need_collection BEFORE INSERT ON publication_preparations
WHEN NOT EXISTS (
    SELECT 1 FROM publications AS p JOIN collections AS c ON c.family = p.family
    WHERE p.id = NEW.publication_id AND p.target = NEW.target
        AND c.id = NEW.collection_id AND c.sealed = 1
)
BEGIN
    SELECT RAISE(ABORT, 'A preparation requires its sealed family collection');
END;

CREATE TRIGGER preparations_keep_content BEFORE UPDATE ON publication_preparations
BEGIN
    SELECT RAISE(ABORT, 'Publication preparations are immutable');
END;

CREATE TRIGGER preparations_keep_history BEFORE DELETE ON publication_preparations
BEGIN
    SELECT RAISE(ABORT, 'Publication preparations are immutable');
END;

CREATE TABLE vote_snapshots (
    id TEXT PRIMARY KEY,
    family TEXT NOT NULL,
    taken_at TEXT NOT NULL,
    closed_at TEXT NOT NULL,
    category_order_json TEXT NOT NULL CHECK (json_valid(category_order_json)),
    closing_revision TEXT,
    expected_store_count INTEGER NOT NULL CHECK (expected_store_count >= 1),
    expected_case_count INTEGER NOT NULL CHECK (expected_case_count >= 0),
    expected_output_count INTEGER NOT NULL CHECK (expected_output_count >= 0),
    expected_vote_count INTEGER NOT NULL CHECK (expected_vote_count >= 0),
    manifest_digest TEXT NOT NULL,
    sealed INTEGER NOT NULL DEFAULT 0 CHECK (sealed IN (0, 1))
) STRICT;

CREATE TABLE vote_snapshot_stores (
    snapshot_id TEXT NOT NULL REFERENCES vote_snapshots (id),
    target TEXT NOT NULL CHECK (target IN ('production', 'private')),
    publication_id TEXT NOT NULL,
    reference TEXT NOT NULL,
    systems_json TEXT NOT NULL CHECK (json_valid(systems_json)),
    defects_json TEXT NOT NULL CHECK (json_valid(defects_json)),
    cutoff INTEGER NOT NULL CHECK (cutoff >= 0),
    excluded INTEGER NOT NULL CHECK (excluded >= 0),
    output_generation_id TEXT REFERENCES collections (id),
    PRIMARY KEY (snapshot_id, target)
) STRICT;

CREATE TABLE vote_snapshot_cases (
    snapshot_id TEXT NOT NULL,
    target TEXT NOT NULL,
    case_key TEXT NOT NULL,
    facts_json TEXT NOT NULL CHECK (json_valid(facts_json)),
    PRIMARY KEY (snapshot_id, target, case_key),
    FOREIGN KEY (snapshot_id, target) REFERENCES vote_snapshot_stores (snapshot_id, target)
) STRICT;

CREATE TABLE vote_snapshot_outputs (
    snapshot_id TEXT NOT NULL,
    target TEXT NOT NULL,
    output_key TEXT NOT NULL,
    facts_json TEXT NOT NULL CHECK (json_valid(facts_json)),
    PRIMARY KEY (snapshot_id, target, output_key),
    FOREIGN KEY (snapshot_id, target) REFERENCES vote_snapshot_stores (snapshot_id, target)
) STRICT;

CREATE TABLE vote_snapshot_votes (
    snapshot_id TEXT NOT NULL,
    target TEXT NOT NULL,
    vote_id INTEGER NOT NULL,
    vote_json TEXT NOT NULL CHECK (json_valid(vote_json)),
    PRIMARY KEY (snapshot_id, target, vote_id),
    FOREIGN KEY (snapshot_id, target) REFERENCES vote_snapshot_stores (snapshot_id, target)
) STRICT;

CREATE TRIGGER snapshots_keep_identity BEFORE UPDATE ON vote_snapshots
WHEN OLD.id IS NOT NEW.id OR OLD.family IS NOT NEW.family
    OR OLD.taken_at IS NOT NEW.taken_at
    OR OLD.closed_at IS NOT NEW.closed_at
    OR OLD.category_order_json IS NOT NEW.category_order_json
    OR OLD.closing_revision IS NOT NEW.closing_revision
    OR OLD.expected_store_count IS NOT NEW.expected_store_count
    OR OLD.expected_case_count IS NOT NEW.expected_case_count
    OR OLD.expected_output_count IS NOT NEW.expected_output_count
    OR OLD.expected_vote_count IS NOT NEW.expected_vote_count
    OR OLD.manifest_digest IS NOT NEW.manifest_digest
    OR (OLD.sealed = 1 AND NEW.sealed != 1)
BEGIN
    SELECT RAISE(ABORT, 'Vote snapshot identity is immutable');
END;

CREATE TRIGGER snapshots_need_complete_rows BEFORE UPDATE OF sealed ON vote_snapshots
WHEN NEW.sealed = 1 AND (
    (SELECT count(*) FROM vote_snapshot_stores WHERE snapshot_id = NEW.id) != NEW.expected_store_count
    OR (SELECT count(*) FROM vote_snapshot_cases WHERE snapshot_id = NEW.id) != NEW.expected_case_count
    OR (SELECT count(*) FROM vote_snapshot_outputs WHERE snapshot_id = NEW.id) != NEW.expected_output_count
    OR (SELECT count(*) FROM vote_snapshot_votes WHERE snapshot_id = NEW.id) != NEW.expected_vote_count
)
BEGIN
    SELECT RAISE(ABORT, 'A vote snapshot must contain every expected row');
END;

CREATE TRIGGER snapshots_keep_history BEFORE DELETE ON vote_snapshots
BEGIN
    SELECT RAISE(ABORT, 'Vote snapshots are immutable');
END;

CREATE TRIGGER snapshot_stores_unsealed_insert BEFORE INSERT ON vote_snapshot_stores
WHEN (SELECT sealed FROM vote_snapshots WHERE id = NEW.snapshot_id) = 1
BEGIN
    SELECT RAISE(ABORT, 'A sealed vote snapshot cannot receive stores');
END;

CREATE TRIGGER snapshot_stores_keep_content BEFORE UPDATE ON vote_snapshot_stores
BEGIN
    SELECT RAISE(ABORT, 'Vote snapshot stores are immutable');
END;

CREATE TRIGGER snapshot_stores_keep_history BEFORE DELETE ON vote_snapshot_stores
BEGIN
    SELECT RAISE(ABORT, 'Vote snapshot stores are immutable');
END;

CREATE TRIGGER snapshot_cases_unsealed_insert BEFORE INSERT ON vote_snapshot_cases
WHEN (SELECT sealed FROM vote_snapshots WHERE id = NEW.snapshot_id) = 1
BEGIN
    SELECT RAISE(ABORT, 'A sealed vote snapshot cannot receive cases');
END;

CREATE TRIGGER snapshot_cases_keep_content BEFORE UPDATE ON vote_snapshot_cases
BEGIN
    SELECT RAISE(ABORT, 'Vote snapshot cases are immutable');
END;

CREATE TRIGGER snapshot_cases_keep_history BEFORE DELETE ON vote_snapshot_cases
BEGIN
    SELECT RAISE(ABORT, 'Vote snapshot cases are immutable');
END;

CREATE TRIGGER snapshot_outputs_unsealed_insert BEFORE INSERT ON vote_snapshot_outputs
WHEN (SELECT sealed FROM vote_snapshots WHERE id = NEW.snapshot_id) = 1
BEGIN
    SELECT RAISE(ABORT, 'A sealed vote snapshot cannot receive outputs');
END;

CREATE TRIGGER snapshot_outputs_keep_content BEFORE UPDATE ON vote_snapshot_outputs
BEGIN
    SELECT RAISE(ABORT, 'Vote snapshot outputs are immutable');
END;

CREATE TRIGGER snapshot_outputs_keep_history BEFORE DELETE ON vote_snapshot_outputs
BEGIN
    SELECT RAISE(ABORT, 'Vote snapshot outputs are immutable');
END;

CREATE TRIGGER snapshot_votes_unsealed_insert BEFORE INSERT ON vote_snapshot_votes
WHEN (SELECT sealed FROM vote_snapshots WHERE id = NEW.snapshot_id) = 1
BEGIN
    SELECT RAISE(ABORT, 'A sealed vote snapshot cannot receive votes');
END;

CREATE TRIGGER snapshot_votes_keep_content BEFORE UPDATE ON vote_snapshot_votes
BEGIN
    SELECT RAISE(ABORT, 'Vote snapshot votes are immutable');
END;

CREATE TRIGGER snapshot_votes_keep_history BEFORE DELETE ON vote_snapshot_votes
BEGIN
    SELECT RAISE(ABORT, 'Vote snapshot votes are immutable');
END;
