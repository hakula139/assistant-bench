CREATE TABLE source_heads (
    scope TEXT PRIMARY KEY,
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0)
) STRICT;

CREATE TABLE runs (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    job TEXT NOT NULL,
    code_revision TEXT NOT NULL,
    case_revision TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'prepared'
        CHECK (status IN ('prepared', 'running', 'complete', 'uncertain')),
    UNIQUE (scope, job)
) STRICT;

CREATE TRIGGER runs_source_head AFTER INSERT ON runs
BEGIN
    INSERT INTO source_heads (scope) VALUES (NEW.scope) ON CONFLICT (scope) DO NOTHING;
END;

CREATE TABLE attempt_claims (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs (id),
    scope TEXT NOT NULL,
    slot_key TEXT NOT NULL,
    trial TEXT NOT NULL,
    system TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    accounting_json TEXT NOT NULL CHECK (json_valid(accounting_json)),
    expected_source_revision INTEGER NOT NULL CHECK (expected_source_revision >= 0),
    created_at TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'claimed'
        CHECK (state IN ('claimed', 'uncertain', 'settled'))
) STRICT;

CREATE UNIQUE INDEX attempt_claims_active_slot ON attempt_claims (scope, slot_key)
    WHERE state IN ('claimed', 'uncertain');
CREATE INDEX attempt_claims_run ON attempt_claims (run_id, state);

CREATE TRIGGER claims_start_run AFTER INSERT ON attempt_claims
BEGIN
    UPDATE runs SET status = 'running' WHERE id = NEW.run_id AND status = 'prepared';
END;

CREATE TRIGGER claims_keep_identity BEFORE UPDATE ON attempt_claims
WHEN OLD.id IS NOT NEW.id
    OR OLD.run_id IS NOT NEW.run_id
    OR OLD.scope IS NOT NEW.scope
    OR OLD.slot_key IS NOT NEW.slot_key
    OR OLD.trial IS NOT NEW.trial
    OR OLD.system IS NOT NEW.system
    OR OLD.fingerprint IS NOT NEW.fingerprint
    OR OLD.accounting_json IS NOT NEW.accounting_json
    OR OLD.expected_source_revision IS NOT NEW.expected_source_revision
    OR OLD.created_at IS NOT NEW.created_at
    OR (OLD.state = 'settled' AND NEW.state != 'settled')
BEGIN
    SELECT RAISE(ABORT, 'Attempt claim identity is immutable');
END;

CREATE TRIGGER claims_keep_history BEFORE DELETE ON attempt_claims
BEGIN
    SELECT RAISE(ABORT, 'Attempt claim history is immutable');
END;

CREATE TRIGGER claims_uncertain_run AFTER UPDATE OF state ON attempt_claims
WHEN NEW.state = 'uncertain'
BEGIN
    UPDATE runs SET status = 'uncertain' WHERE id = NEW.run_id AND status != 'complete';
END;

CREATE TABLE attempt_entries (
    scope TEXT NOT NULL,
    job TEXT NOT NULL,
    trial TEXT NOT NULL,
    started_at TEXT NOT NULL,
    claim_id TEXT NOT NULL REFERENCES attempt_claims (id),
    entry_json TEXT NOT NULL CHECK (json_valid(entry_json)),
    PRIMARY KEY (scope, job, trial, started_at)
) STRICT;

CREATE INDEX attempt_entries_claim ON attempt_entries (claim_id);

CREATE TRIGGER entries_keep_content BEFORE UPDATE ON attempt_entries
BEGIN
    SELECT RAISE(ABORT, 'Attempt ledger entries are immutable');
END;

CREATE TRIGGER entries_keep_history BEFORE DELETE ON attempt_entries
BEGIN
    SELECT RAISE(ABORT, 'Attempt ledger history is immutable');
END;

CREATE TABLE claim_artifacts (
    claim_id TEXT PRIMARY KEY REFERENCES attempt_claims (id),
    commit_sha TEXT NOT NULL,
    path TEXT NOT NULL,
    manifest_digest TEXT NOT NULL
) STRICT;

CREATE TRIGGER artifacts_need_entry BEFORE INSERT ON claim_artifacts
WHEN NOT EXISTS (SELECT 1 FROM attempt_entries WHERE claim_id = NEW.claim_id)
BEGIN
    SELECT RAISE(ABORT, 'Claim artifacts require a recorded attempt');
END;

CREATE TRIGGER artifacts_settle_claim AFTER INSERT ON claim_artifacts
BEGIN
    UPDATE attempt_claims SET state = 'settled' WHERE id = NEW.claim_id;
END;

CREATE TRIGGER artifacts_keep_content BEFORE UPDATE ON claim_artifacts
BEGIN
    SELECT RAISE(ABORT, 'Claim artifact references are immutable');
END;

CREATE TRIGGER artifacts_keep_history BEFORE DELETE ON claim_artifacts
BEGIN
    SELECT RAISE(ABORT, 'Claim artifact history is immutable');
END;

CREATE TRIGGER claims_need_artifacts BEFORE UPDATE OF state ON attempt_claims
WHEN NEW.state = 'settled'
    AND (
        NOT EXISTS (SELECT 1 FROM claim_artifacts WHERE claim_id = NEW.id)
        OR NOT EXISTS (SELECT 1 FROM attempt_entries WHERE claim_id = NEW.id)
    )
BEGIN
    SELECT RAISE(ABORT, 'A settled claim requires its ledger and artifacts');
END;

CREATE TRIGGER claims_advance_source AFTER UPDATE OF state ON attempt_claims
WHEN OLD.state != 'settled' AND NEW.state = 'settled'
BEGIN
    UPDATE source_heads SET revision = revision + 1 WHERE scope = NEW.scope;
END;

CREATE TABLE collections (
    id TEXT PRIMARY KEY,
    family TEXT NOT NULL,
    expected_count INTEGER NOT NULL CHECK (expected_count >= 0),
    manifest_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sealed INTEGER NOT NULL DEFAULT 0 CHECK (sealed IN (0, 1)),
    UNIQUE (id, family)
) STRICT;

CREATE TRIGGER collections_keep_identity BEFORE UPDATE ON collections
WHEN OLD.id IS NOT NEW.id
    OR OLD.family IS NOT NEW.family
    OR OLD.expected_count IS NOT NEW.expected_count
    OR OLD.manifest_digest IS NOT NEW.manifest_digest
    OR OLD.created_at IS NOT NEW.created_at
    OR (OLD.sealed = 1 AND NEW.sealed != 1)
BEGIN
    SELECT RAISE(ABORT, 'Collection identity is immutable');
END;

CREATE TRIGGER collections_keep_history BEFORE DELETE ON collections
BEGIN
    SELECT RAISE(ABORT, 'Collection history is immutable');
END;

CREATE TABLE collection_outputs (
    collection_id TEXT NOT NULL REFERENCES collections (id),
    position INTEGER NOT NULL CHECK (position >= 0),
    output_key TEXT NOT NULL,
    output_json TEXT NOT NULL CHECK (json_valid(output_json)),
    PRIMARY KEY (collection_id, position),
    UNIQUE (collection_id, output_key)
) STRICT;

CREATE TRIGGER outputs_unsealed_insert BEFORE INSERT ON collection_outputs
WHEN (SELECT sealed FROM collections WHERE id = NEW.collection_id) = 1
BEGIN
    SELECT RAISE(ABORT, 'A sealed collection cannot receive outputs');
END;

CREATE TRIGGER outputs_keep_content BEFORE UPDATE ON collection_outputs
BEGIN
    SELECT RAISE(ABORT, 'Collected outputs are immutable');
END;

CREATE TRIGGER outputs_keep_history BEFORE DELETE ON collection_outputs
BEGIN
    SELECT RAISE(ABORT, 'Collected outputs are immutable');
END;

CREATE TABLE active_collections (
    family TEXT PRIMARY KEY,
    collection_id TEXT NOT NULL,
    FOREIGN KEY (collection_id, family) REFERENCES collections (id, family)
) STRICT;

CREATE TRIGGER active_needs_sealed_insert BEFORE INSERT ON active_collections
WHEN NOT EXISTS (
    SELECT 1 FROM collections
    WHERE id = NEW.collection_id AND family = NEW.family AND sealed = 1
)
BEGIN
    SELECT RAISE(ABORT, 'An active collection must be sealed');
END;

CREATE TRIGGER active_needs_sealed_update BEFORE UPDATE ON active_collections
WHEN NOT EXISTS (
    SELECT 1 FROM collections
    WHERE id = NEW.collection_id AND family = NEW.family AND sealed = 1
)
BEGIN
    SELECT RAISE(ABORT, 'An active collection must be sealed');
END;

CREATE TABLE canary_records (
    run_id TEXT PRIMARY KEY REFERENCES runs (id),
    system TEXT NOT NULL,
    record_json TEXT NOT NULL CHECK (json_valid(record_json))
) STRICT;

CREATE INDEX canary_records_system ON canary_records (system);

CREATE TRIGGER canaries_keep_content BEFORE UPDATE ON canary_records
BEGIN
    SELECT RAISE(ABORT, 'Canary records are immutable');
END;

CREATE TRIGGER canaries_keep_history BEFORE DELETE ON canary_records
BEGIN
    SELECT RAISE(ABORT, 'Canary history is immutable');
END;

CREATE TABLE canary_baselines (
    system TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES canary_records (run_id)
) STRICT;
