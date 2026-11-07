CREATE TABLE source_heads (
    scope TEXT PRIMARY KEY,
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0)
) STRICT;

CREATE TABLE runs (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    job TEXT NOT NULL,
    code_revision TEXT,
    case_revision TEXT,
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
        CHECK (state IN ('claimed', 'uncertain', 'settled', 'cancelled'))
) STRICT;

CREATE UNIQUE INDEX attempt_claims_active_slot ON attempt_claims (scope, slot_key)
    WHERE state IN ('claimed', 'uncertain');
CREATE INDEX attempt_claims_scope ON attempt_claims (scope);
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
    OR (OLD.state IN ('settled', 'cancelled') AND NEW.state != OLD.state)
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

CREATE TABLE attempt_starts (
    id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL REFERENCES attempt_claims (id),
    started_at TEXT NOT NULL,
    UNIQUE (claim_id, started_at)
) STRICT;

CREATE TRIGGER starts_need_live_claim BEFORE INSERT ON attempt_starts
WHEN NOT EXISTS (
    SELECT 1 FROM attempt_claims WHERE id = NEW.claim_id AND state = 'claimed'
)
BEGIN
    SELECT RAISE(ABORT, 'An attempt start requires a claimed slot');
END;

CREATE TRIGGER starts_keep_content BEFORE UPDATE ON attempt_starts
BEGIN
    SELECT RAISE(ABORT, 'Attempt starts are immutable');
END;

CREATE TRIGGER starts_keep_history BEFORE DELETE ON attempt_starts
BEGIN
    SELECT RAISE(ABORT, 'Attempt start history is immutable');
END;

CREATE TRIGGER claims_cancel_before_start BEFORE UPDATE OF state ON attempt_claims
WHEN NEW.state = 'cancelled'
    AND EXISTS (SELECT 1 FROM attempt_starts WHERE claim_id = NEW.id)
BEGIN
    SELECT RAISE(ABORT, 'A started claim cannot be cancelled');
END;

CREATE TABLE attempt_entries (
    scope TEXT NOT NULL,
    job TEXT NOT NULL,
    trial TEXT NOT NULL,
    started_at TEXT NOT NULL,
    claim_id TEXT NOT NULL REFERENCES attempt_claims (id),
    attempt_id TEXT NOT NULL UNIQUE REFERENCES attempt_starts (id),
    entry_json TEXT NOT NULL CHECK (json_valid(entry_json)),
    PRIMARY KEY (scope, job, trial, started_at)
) STRICT;

CREATE INDEX attempt_entries_claim ON attempt_entries (claim_id);

CREATE TRIGGER entries_need_start BEFORE INSERT ON attempt_entries
WHEN NOT EXISTS (
    SELECT 1 FROM attempt_starts AS starts
    JOIN attempt_claims AS claims ON claims.id = starts.claim_id
    JOIN runs ON runs.id = claims.run_id
    WHERE starts.id = NEW.attempt_id AND starts.claim_id = NEW.claim_id
        AND starts.started_at = NEW.started_at AND claims.scope = NEW.scope
        AND runs.job = NEW.job AND claims.trial = NEW.trial
        AND claims.state IN ('claimed', 'uncertain')
)
BEGIN
    SELECT RAISE(ABORT, 'A ledger entry requires its live attempt start');
END;

CREATE TRIGGER entries_keep_content BEFORE UPDATE ON attempt_entries
BEGIN
    SELECT RAISE(ABORT, 'Attempt ledger entries are immutable');
END;

CREATE TRIGGER entries_keep_history BEFORE DELETE ON attempt_entries
BEGIN
    SELECT RAISE(ABORT, 'Attempt ledger history is immutable');
END;

CREATE TABLE request_admissions (
    id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL REFERENCES attempt_starts (id),
    created_at TEXT NOT NULL
) STRICT;

CREATE INDEX request_admissions_attempt ON request_admissions (attempt_id);

CREATE TRIGGER requests_need_live_attempt BEFORE INSERT ON request_admissions
WHEN NOT EXISTS (
    SELECT 1 FROM attempt_starts AS starts
    JOIN attempt_claims AS claims ON claims.id = starts.claim_id
    WHERE starts.id = NEW.attempt_id AND claims.state = 'claimed'
        AND NOT EXISTS (
            SELECT 1 FROM attempt_entries WHERE attempt_id = starts.id
        )
)
BEGIN
    SELECT RAISE(ABORT, 'A request requires a live unfinished attempt');
END;

CREATE TRIGGER requests_keep_content BEFORE UPDATE ON request_admissions
BEGIN
    SELECT RAISE(ABORT, 'Request admissions are immutable');
END;

CREATE TRIGGER requests_keep_history BEFORE DELETE ON request_admissions
BEGIN
    SELECT RAISE(ABORT, 'Request admission history is immutable');
END;

CREATE TABLE request_receipts (
    request_id TEXT PRIMARY KEY REFERENCES request_admissions (id),
    receipt_json TEXT NOT NULL CHECK (json_valid(receipt_json))
) STRICT;

CREATE TRIGGER receipts_keep_content BEFORE UPDATE ON request_receipts
BEGIN
    SELECT RAISE(ABORT, 'Request terminal receipts are immutable');
END;

CREATE TRIGGER receipts_keep_history BEFORE DELETE ON request_receipts
BEGIN
    SELECT RAISE(ABORT, 'Request terminal receipt history is immutable');
END;

CREATE TABLE trial_artifacts (
    run_id TEXT NOT NULL REFERENCES runs (id),
    trial_id TEXT NOT NULL,
    source_trial_id TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    path TEXT NOT NULL,
    manifest_digest TEXT NOT NULL,
    PRIMARY KEY (run_id, trial_id)
) STRICT;

CREATE TRIGGER trial_artifacts_keep_content BEFORE UPDATE ON trial_artifacts
BEGIN
    SELECT RAISE(ABORT, 'Trial artifact references are immutable');
END;

CREATE TRIGGER trial_artifacts_keep_history BEFORE DELETE ON trial_artifacts
BEGIN
    SELECT RAISE(ABORT, 'Trial artifact history is immutable');
END;

CREATE TABLE claim_artifacts (
    claim_id TEXT PRIMARY KEY REFERENCES attempt_claims (id),
    run_id TEXT NOT NULL,
    trial_id TEXT NOT NULL,
    FOREIGN KEY (run_id, trial_id) REFERENCES trial_artifacts (run_id, trial_id)
) STRICT;

CREATE TRIGGER artifacts_need_entry BEFORE INSERT ON claim_artifacts
WHEN NOT EXISTS (
    SELECT 1 FROM attempt_claims
    WHERE id = NEW.claim_id AND run_id = NEW.run_id AND trial = NEW.trial_id
        AND state IN ('claimed', 'uncertain')
) OR NOT EXISTS (SELECT 1 FROM attempt_entries WHERE claim_id = NEW.claim_id)
    OR EXISTS (
        SELECT 1 FROM request_admissions AS requests
        JOIN attempt_starts AS starts ON starts.id = requests.attempt_id
        LEFT JOIN request_receipts AS receipts ON receipts.request_id = requests.id
        WHERE starts.claim_id = NEW.claim_id AND receipts.request_id IS NULL
    )
BEGIN
    SELECT RAISE(ABORT, 'Claim artifacts require their live claim, Entry and receipts');
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
        OR EXISTS (
            SELECT 1 FROM request_admissions AS requests
            JOIN attempt_starts AS starts ON starts.id = requests.attempt_id
            LEFT JOIN request_receipts AS receipts ON receipts.request_id = requests.id
            WHERE starts.claim_id = NEW.id AND receipts.request_id IS NULL
        )
    )
BEGIN
    SELECT RAISE(ABORT, 'A settled claim requires its ledger, artifacts and receipts');
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
