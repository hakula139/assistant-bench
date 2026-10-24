CREATE TABLE actor_expiry (
    actor_id TEXT PRIMARY KEY REFERENCES actors(id),
    expires_at INTEGER NOT NULL
) STRICT;

CREATE TABLE session_expiry (
    session_id TEXT PRIMARY KEY REFERENCES sessions(id),
    expires_at INTEGER NOT NULL
) STRICT;

CREATE TABLE claim_expiry (
    claim_id TEXT PRIMARY KEY REFERENCES claims(id),
    expires_at INTEGER NOT NULL
) STRICT;

CREATE INDEX actor_expiry_due ON actor_expiry(expires_at, actor_id);
CREATE INDEX session_expiry_due ON session_expiry(expires_at, session_id);
CREATE INDEX claim_expiry_due ON claim_expiry(expires_at, claim_id);
CREATE INDEX events_principal ON evidence_events(principal);
CREATE INDEX sessions_actor ON sessions(actor_id);
CREATE INDEX credentials_actor ON credentials(actor_id);
CREATE INDEX admissions_actor ON admission_results(actor_id);
CREATE INDEX claims_canonical_actor ON claims(canonical_actor);

CREATE TRIGGER actor_browsing AFTER INSERT ON actors
WHEN NEW.owner_namespace IS NULL
BEGIN
    INSERT INTO actor_expiry(actor_id, expires_at) VALUES (NEW.id, NEW.created_at + 2592000000);
END;

CREATE TRIGGER session_browsing AFTER INSERT ON sessions
WHEN NEW.import_source IS NULL
BEGIN
    INSERT INTO session_expiry(session_id, expires_at) VALUES (NEW.id, NEW.created_at + 2592000000);
    UPDATE actor_expiry SET expires_at = max(expires_at, NEW.created_at + 2592000000)
    WHERE actor_id = NEW.actor_id;
END;

CREATE TRIGGER credential_browsing AFTER INSERT ON credentials
BEGIN
    UPDATE actor_expiry SET expires_at = max(expires_at, NEW.expires_at) WHERE actor_id = NEW.actor_id;
END;

CREATE TRIGGER claim_browsing AFTER INSERT ON pair_leases
WHEN NOT EXISTS (SELECT 1 FROM vote_receipts WHERE claim_id = NEW.claim_id)
BEGIN
    INSERT INTO claim_expiry(claim_id, expires_at) VALUES (NEW.claim_id, NEW.expires_at + 604800000)
    ON CONFLICT(claim_id) DO UPDATE SET expires_at = max(expires_at, excluded.expires_at);
    UPDATE actor_expiry SET expires_at = max(expires_at, NEW.expires_at + 604800000)
    WHERE actor_id = NEW.actor_id;
    UPDATE session_expiry SET expires_at = max(expires_at, NEW.expires_at + 604800000)
    WHERE session_id = NEW.session_id;
END;

CREATE TRIGGER receipt_browsing_complete AFTER INSERT ON vote_receipts
BEGIN
    DELETE FROM actor_expiry WHERE actor_id = NEW.actor_id;
    DELETE FROM session_expiry WHERE session_id = NEW.session_id;
    DELETE FROM claim_expiry WHERE claim_id = NEW.claim_id;
END;

CREATE TRIGGER actor_audited AFTER INSERT ON evidence_events
BEGIN DELETE FROM actor_expiry WHERE actor_id = NEW.principal; END;

CREATE TRIGGER attribution_browsing_complete AFTER INSERT ON attribution_plans
BEGIN DELETE FROM actor_expiry WHERE actor_id IN (NEW.actor_id, NEW.owner_id); END;

CREATE TRIGGER claim_audited AFTER INSERT ON attribution_targets
BEGIN DELETE FROM claim_expiry WHERE claim_id = NEW.claim_id; END;

CREATE TRIGGER claims_delete_expiry BEFORE DELETE ON claims
BEGIN DELETE FROM claim_expiry WHERE claim_id = OLD.id; END;

DROP TRIGGER sessions_delete_state;
CREATE TRIGGER sessions_delete_state BEFORE DELETE ON sessions
BEGIN
    DELETE FROM session_expiry WHERE session_id = OLD.id;
    DELETE FROM session_state WHERE session_id = OLD.id;
END;

DROP TRIGGER actors_no_delete;
CREATE TRIGGER actors_no_delete BEFORE DELETE ON actors
WHEN OLD.owner_namespace IS NOT NULL
    OR EXISTS (SELECT 1 FROM vote_receipts WHERE actor_id = OLD.id)
    OR EXISTS (SELECT 1 FROM evidence_events WHERE principal = OLD.id)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER actors_delete_state BEFORE DELETE ON actors
BEGIN
    DELETE FROM actor_expiry WHERE actor_id = OLD.id;
    DELETE FROM actor_resolution WHERE actor_id = OLD.id AND canonical_actor = OLD.id;
END;
