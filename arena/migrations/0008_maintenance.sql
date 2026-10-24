CREATE INDEX admission_original_credential ON admission_results(original_credential);
CREATE INDEX leases_pending_expiry ON pair_leases(expires_at, id) WHERE status = 'pending';
CREATE INDEX sessions_created ON sessions(created_at, id);
CREATE INDEX actors_created ON actors(created_at, id);

CREATE TABLE lease_revocations (
    id TEXT PRIMARY KEY REFERENCES pair_leases(id)
) STRICT;

CREATE VIEW expire_leases AS SELECT NULL AS now_ms WHERE 0;

CREATE TRIGGER expire_leases_insert INSTEAD OF INSERT ON expire_leases
BEGIN
    INSERT INTO lease_revocations(id)
    SELECT id FROM pair_leases WHERE status = 'pending' AND expires_at <= NEW.now_ms
    ORDER BY expires_at, id LIMIT 250;

    UPDATE pair_leases SET status = 'revoked' WHERE id IN (SELECT id FROM lease_revocations);

    UPDATE claims SET current_lease = NULL, version = version + 1
    WHERE current_lease IN (SELECT id FROM lease_revocations);

    UPDATE session_state SET current_lease = NULL, version = version + 1
    WHERE current_lease IN (SELECT id FROM lease_revocations);

    DELETE FROM lease_revocations;
END;

DROP TRIGGER sessions_no_delete;
CREATE TRIGGER sessions_no_delete BEFORE DELETE ON sessions
WHEN OLD.import_source IS NOT NULL
    OR EXISTS (SELECT 1 FROM vote_receipts WHERE session_id = OLD.id)
    OR EXISTS (SELECT 1 FROM pair_leases WHERE session_id = OLD.id)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER sessions_delete_state BEFORE DELETE ON sessions
BEGIN DELETE FROM session_state WHERE session_id = OLD.id; END;
