CREATE TABLE pending_trial_artifacts (
    run_id TEXT NOT NULL REFERENCES runs (id),
    trial_id TEXT NOT NULL,
    source_trial_id TEXT NOT NULL,
    staged_commit_sha TEXT NOT NULL,
    path TEXT NOT NULL,
    manifest_digest TEXT NOT NULL,
    authorized_claim_id TEXT UNIQUE REFERENCES attempt_claims (id),
    publication_ready INTEGER NOT NULL DEFAULT 0 CHECK (publication_ready IN (0, 1)),
    canonical_commit_sha TEXT,
    PRIMARY KEY (run_id, trial_id)
) STRICT;

CREATE TRIGGER pending_need_unsealed_run BEFORE INSERT ON pending_trial_artifacts
WHEN NEW.authorized_claim_id IS NOT NULL OR NEW.canonical_commit_sha IS NOT NULL
    OR NEW.publication_ready != 0 OR EXISTS (
    SELECT 1 FROM pending_trial_artifacts
    WHERE run_id = NEW.run_id AND publication_ready = 1
)
BEGIN
    SELECT RAISE(ABORT, 'A sealed publication cannot receive new trials');
END;

CREATE TRIGGER pending_keep_identity BEFORE UPDATE ON pending_trial_artifacts
WHEN OLD.run_id IS NOT NEW.run_id
    OR OLD.trial_id IS NOT NEW.trial_id
    OR OLD.source_trial_id IS NOT NEW.source_trial_id
    OR OLD.staged_commit_sha IS NOT NEW.staged_commit_sha
    OR OLD.path IS NOT NEW.path
    OR OLD.manifest_digest IS NOT NEW.manifest_digest
    OR (OLD.authorized_claim_id IS NOT NULL
        AND OLD.authorized_claim_id IS NOT NEW.authorized_claim_id)
    OR (OLD.publication_ready = 1 AND NEW.publication_ready != 1)
    OR (OLD.canonical_commit_sha IS NOT NULL
        AND OLD.canonical_commit_sha IS NOT NEW.canonical_commit_sha)
BEGIN
    SELECT RAISE(ABORT, 'Pending artifact identity is immutable');
END;

CREATE TRIGGER pending_keep_history BEFORE DELETE ON pending_trial_artifacts
BEGIN
    SELECT RAISE(ABORT, 'Pending artifact history is immutable');
END;

CREATE TRIGGER pending_need_claim BEFORE UPDATE OF authorized_claim_id
ON pending_trial_artifacts
WHEN NEW.authorized_claim_id IS NOT OLD.authorized_claim_id
    AND (
        OLD.publication_ready = 1
        OR NOT EXISTS (
            SELECT 1 FROM attempt_claims
            WHERE id = NEW.authorized_claim_id AND run_id = NEW.run_id
                AND trial = NEW.trial_id AND state = 'claimed'
        )
        OR NOT EXISTS (
            SELECT 1 FROM attempt_entries WHERE claim_id = NEW.authorized_claim_id
        )
        OR EXISTS (
            SELECT 1 FROM request_admissions AS requests
            JOIN attempt_starts AS starts ON starts.id = requests.attempt_id
            LEFT JOIN request_receipts AS receipts ON receipts.request_id = requests.id
            WHERE starts.claim_id = NEW.authorized_claim_id
                AND receipts.request_id IS NULL
        )
    )
BEGIN
    SELECT RAISE(ABORT, 'Publication authorization requires a normal complete attempt');
END;

CREATE TRIGGER pending_need_ready BEFORE UPDATE OF canonical_commit_sha
ON pending_trial_artifacts
WHEN NEW.canonical_commit_sha IS NOT NULL AND NEW.publication_ready != 1
BEGIN
    SELECT RAISE(ABORT, 'Only sealed publications can become canonical');
END;

CREATE TRIGGER pending_finalize AFTER UPDATE OF canonical_commit_sha
ON pending_trial_artifacts
WHEN OLD.canonical_commit_sha IS NULL AND NEW.canonical_commit_sha IS NOT NULL
BEGIN
    INSERT INTO trial_artifacts (
        run_id, trial_id, source_trial_id, commit_sha, path, manifest_digest
    ) VALUES (
        NEW.run_id, NEW.trial_id, NEW.source_trial_id, NEW.canonical_commit_sha,
        NEW.path, NEW.manifest_digest
    );
    INSERT INTO claim_artifacts (claim_id, run_id, trial_id)
    SELECT NEW.authorized_claim_id, NEW.run_id, NEW.trial_id
    WHERE NEW.authorized_claim_id IS NOT NULL;
END;
