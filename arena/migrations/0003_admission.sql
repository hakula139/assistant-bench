CREATE TABLE quota_windows (
    operation TEXT NOT NULL,
    scope TEXT NOT NULL,
    subject TEXT NOT NULL,
    window_ms INTEGER NOT NULL CHECK (window_ms > 0),
    window_start INTEGER NOT NULL CHECK (window_start >= 0),
    consumed INTEGER NOT NULL CHECK (consumed >= 0),
    expires_at INTEGER NOT NULL CHECK (expires_at >= window_start),
    PRIMARY KEY (operation, scope, subject, window_ms, window_start),
    FOREIGN KEY (operation, scope, window_ms) REFERENCES quota_policies(operation, scope, window_ms)
) STRICT;

CREATE INDEX quota_expiry ON quota_windows(expires_at);

CREATE TABLE credentials (
    id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL REFERENCES actors(id),
    rate_subject TEXT NOT NULL,
    issued_at INTEGER NOT NULL CHECK (issued_at >= 0),
    expires_at INTEGER NOT NULL CHECK (expires_at > issued_at),
    challenge_until INTEGER NOT NULL CHECK (challenge_until >= issued_at AND challenge_until <= expires_at),
    revoked_at INTEGER CHECK (revoked_at >= issued_at),
    renewal_command TEXT UNIQUE,
    CHECK (renewal_command IS NULL OR revoked_at IS NOT NULL)
) STRICT;

CREATE INDEX credential_expiry ON credentials(expires_at);

CREATE TABLE admission_results (
    command_id TEXT PRIMARY KEY,
    request_hash TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('new', 'renew')),
    context_digest TEXT NOT NULL,
    proof_digest TEXT NOT NULL UNIQUE,
    original_credential TEXT,
    actor_id TEXT NOT NULL REFERENCES actors(id),
    credential_id TEXT NOT NULL UNIQUE REFERENCES credentials(id),
    rate_subject TEXT NOT NULL,
    issued_at INTEGER NOT NULL CHECK (issued_at >= 0),
    expires_at INTEGER NOT NULL CHECK (expires_at > issued_at),
    challenge_until INTEGER NOT NULL CHECK (challenge_until >= issued_at AND challenge_until <= expires_at),
    CHECK ((operation = 'renew') = (original_credential IS NOT NULL))
) STRICT;

CREATE INDEX admission_expiry ON admission_results(expires_at);

CREATE TRIGGER admission_no_update BEFORE UPDATE ON admission_results
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER admission_no_replace BEFORE INSERT ON admission_results
WHEN EXISTS (SELECT 1 FROM admission_results WHERE command_id = NEW.command_id
    OR proof_digest = NEW.proof_digest OR credential_id = NEW.credential_id)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER credentials_no_replace BEFORE INSERT ON credentials
WHEN EXISTS (SELECT 1 FROM credentials WHERE id = NEW.id OR renewal_command = NEW.renewal_command)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER credentials_guard BEFORE UPDATE ON credentials
WHEN NEW.id != OLD.id OR NEW.actor_id != OLD.actor_id OR NEW.rate_subject != OLD.rate_subject
    OR NEW.issued_at != OLD.issued_at OR NEW.expires_at != OLD.expires_at
    OR NEW.challenge_until != OLD.challenge_until OR OLD.revoked_at IS NOT NULL
    OR NEW.revoked_at IS NULL
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TABLE network_attribution (
    receipt_seq INTEGER NOT NULL REFERENCES vote_receipts(seq),
    key_version TEXT NOT NULL,
    tag_kind TEXT NOT NULL CHECK (tag_kind IN ('exact', 'prefix')),
    tag TEXT NOT NULL,
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    expires_at INTEGER NOT NULL CHECK (expires_at > created_at),
    PRIMARY KEY (receipt_seq, key_version, tag_kind)
) STRICT;

CREATE INDEX attribution_tag ON network_attribution(key_version, tag_kind, tag, receipt_seq);
CREATE INDEX attribution_expiry ON network_attribution(expires_at);

CREATE TABLE private_annotations (
    receipt_seq INTEGER PRIMARY KEY REFERENCES vote_receipts(seq),
    note TEXT CHECK (length(note) <= 500),
    redacted_at INTEGER CHECK (redacted_at >= 0),
    CHECK (redacted_at IS NULL OR note IS NULL)
) STRICT;

CREATE TRIGGER annotations_local BEFORE INSERT ON private_annotations
WHEN (SELECT mode FROM deployment WHERE id = 1) != 'local'
BEGIN SELECT RAISE(ABORT, 'private_material'); END;

CREATE TRIGGER annotations_no_recreation BEFORE UPDATE ON private_annotations
WHEN OLD.redacted_at IS NOT NULL AND (NEW.note IS NOT NULL OR NEW.redacted_at IS NULL)
BEGIN SELECT RAISE(ABORT, 'redacted'); END;

CREATE TRIGGER annotations_no_replace BEFORE INSERT ON private_annotations
WHEN EXISTS (SELECT 1 FROM private_annotations WHERE receipt_seq = NEW.receipt_seq)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER annotations_no_delete BEFORE DELETE ON private_annotations
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE VIEW spend_quotas AS
SELECT NULL AS operation, NULL AS actor_id, NULL AS family, NULL AS now_ms,
    NULL AS network WHERE 0;

CREATE TRIGGER spend_quotas_insert INSTEAD OF INSERT ON spend_quotas
WHEN (SELECT mode FROM deployment WHERE id = 1) = 'public'
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM quota_policies WHERE operation = NEW.operation
    ) THEN RAISE(ABORT, 'quota_policy') END;

    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM quota_policies p WHERE p.operation = NEW.operation
            AND p.scope IN ('exact', 'prefix')
            AND (SELECT count(*) FROM json_each(NEW.network) n
                WHERE json_extract(n.value, '$.scope') = p.scope
                    AND json_extract(n.value, '$.window_ms') = p.window_ms
                    AND json_type(n.value, '$.tag') = 'text'
                    AND length(json_extract(n.value, '$.tag')) > 0) != 1
    ) THEN RAISE(ABORT, 'network_context') END;

    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM quota_policies p WHERE p.operation = NEW.operation
            AND COALESCE((SELECT sum(q.consumed) FROM quota_windows q
                WHERE q.operation = p.operation AND q.scope = p.scope
                    AND q.window_ms = p.window_ms
                    AND q.window_start = CAST(NEW.now_ms / p.window_ms AS INTEGER) * p.window_ms
                    AND CASE p.scope
                        WHEN 'actor' THEN q.subject IN (
                            SELECT actor_id FROM actor_resolution
                            WHERE family = NEW.family AND canonical_actor = COALESCE((
                                SELECT canonical_actor FROM actor_resolution
                                WHERE family = NEW.family AND actor_id = NEW.actor_id
                            ), NEW.actor_id)
                            UNION SELECT NEW.actor_id
                            UNION SELECT COALESCE((SELECT canonical_actor FROM actor_resolution
                                WHERE family = NEW.family AND actor_id = NEW.actor_id), NEW.actor_id)
                        )
                        WHEN 'deployment' THEN q.subject = (SELECT namespace FROM deployment WHERE id = 1)
                        ELSE q.subject = (SELECT json_extract(n.value, '$.tag') FROM json_each(NEW.network) n
                            WHERE json_extract(n.value, '$.scope') = p.scope
                                AND json_extract(n.value, '$.window_ms') = p.window_ms)
                    END), 0) >= p.allowance
    ) THEN RAISE(ABORT, 'quota_exhausted') END;

    INSERT INTO quota_windows(operation, scope, subject, window_ms, window_start, consumed, expires_at)
    SELECT p.operation, p.scope,
        CASE p.scope WHEN 'actor' THEN NEW.actor_id
            WHEN 'deployment' THEN (SELECT namespace FROM deployment WHERE id = 1)
            ELSE (SELECT json_extract(n.value, '$.tag') FROM json_each(NEW.network) n
                WHERE json_extract(n.value, '$.scope') = p.scope
                    AND json_extract(n.value, '$.window_ms') = p.window_ms)
        END,
        p.window_ms, CAST(NEW.now_ms / p.window_ms AS INTEGER) * p.window_ms, 1,
        (CAST(NEW.now_ms / p.window_ms AS INTEGER) + 1) * p.window_ms + 172800000
    FROM quota_policies p WHERE p.operation = NEW.operation
    ON CONFLICT(operation, scope, subject, window_ms, window_start)
    DO UPDATE SET consumed = consumed + 1;
END;

CREATE VIEW admit_credential AS
SELECT NULL AS command_id, NULL AS request_hash, NULL AS operation,
    NULL AS context_digest, NULL AS proof_digest, NULL AS original_credential,
    NULL AS actor_id, NULL AS credential_id, NULL AS rate_subject, NULL AS now_ms,
    NULL AS expires_at, NULL AS challenge_until, NULL AS network WHERE 0;

CREATE TRIGGER admit_credential_insert INSTEAD OF INSERT ON admit_credential
BEGIN
    SELECT CASE WHEN EXISTS (SELECT 1 FROM evidence_events WHERE command_id = NEW.command_id)
        OR EXISTS (SELECT 1 FROM pair_leases WHERE command_id = NEW.command_id)
        THEN RAISE(ABORT, 'conflict') END;
    SELECT CASE WHEN EXISTS (SELECT 1 FROM admission_results WHERE command_id = NEW.command_id)
        THEN RAISE(ABORT, 'command_exists') END;
    SELECT CASE WHEN EXISTS (SELECT 1 FROM admission_results WHERE proof_digest = NEW.proof_digest)
        THEN RAISE(ABORT, 'proof_consumed') END;
    SELECT CASE WHEN (SELECT mode FROM deployment WHERE id = 1) != 'public'
        OR NEW.expires_at <= NEW.now_ms OR NEW.expires_at > NEW.now_ms + 2592000000
        OR NEW.challenge_until < NEW.now_ms OR NEW.challenge_until > NEW.now_ms + 86400000
        OR NEW.challenge_until > NEW.expires_at
        THEN RAISE(ABORT, 'credential_context') END;
    SELECT CASE WHEN NEW.operation NOT IN ('new', 'renew')
        OR (NEW.operation = 'renew') != (NEW.original_credential IS NOT NULL)
        OR (NEW.operation = 'renew' AND NOT EXISTS (
            SELECT 1 FROM credentials WHERE id = NEW.original_credential AND actor_id = NEW.actor_id
                AND revoked_at IS NULL AND expires_at > NEW.now_ms))
        OR (NEW.operation = 'new' AND EXISTS (SELECT 1 FROM actors WHERE id = NEW.actor_id))
        THEN RAISE(ABORT, 'credential_context') END;

    INSERT INTO actors(id, created_at, rate_subject)
    SELECT NEW.actor_id, NEW.now_ms, NEW.rate_subject WHERE NEW.operation = 'new';
    INSERT INTO spend_quotas(operation, actor_id, family, now_ms, network)
    VALUES (CASE NEW.operation WHEN 'new' THEN 'admit' ELSE 'renew' END,
        NEW.actor_id, '', NEW.now_ms, NEW.network);
    UPDATE credentials SET revoked_at = NEW.now_ms, renewal_command = NEW.command_id
    WHERE id = NEW.original_credential AND NEW.operation = 'renew';
    INSERT INTO credentials(id, actor_id, rate_subject, issued_at, expires_at, challenge_until)
    SELECT NEW.credential_id, NEW.actor_id, rate_subject, NEW.now_ms, NEW.expires_at, NEW.challenge_until
    FROM actors WHERE id = NEW.actor_id;
    INSERT INTO admission_results(command_id, request_hash, operation, context_digest,
        proof_digest, original_credential, actor_id, credential_id, rate_subject, issued_at, expires_at, challenge_until)
    SELECT NEW.command_id, NEW.request_hash, NEW.operation, NEW.context_digest,
        NEW.proof_digest, NEW.original_credential, NEW.actor_id, NEW.credential_id,
        rate_subject, NEW.now_ms, NEW.expires_at, NEW.challenge_until
    FROM actors WHERE id = NEW.actor_id;
END;

CREATE VIEW issue_pair AS
SELECT NULL AS command_id, NULL AS request_hash, NULL AS actor_id, NULL AS lease_id,
    NULL AS session_id, NULL AS session_version, NULL AS claim_id, NULL AS claim_version,
    NULL AS case_key, NULL AS a_output, NULL AS b_output, NULL AS comparison_key,
    NULL AS repeat_of, NULL AS now_ms, NULL AS expires_at, NULL AS network WHERE 0;

CREATE TRIGGER issue_pair_insert INSTEAD OF INSERT ON issue_pair
BEGIN
    SELECT CASE WHEN EXISTS (SELECT 1 FROM evidence_events WHERE command_id = NEW.command_id)
        OR EXISTS (SELECT 1 FROM admission_results WHERE command_id = NEW.command_id)
        THEN RAISE(ABORT, 'conflict') END;
    SELECT CASE WHEN EXISTS (SELECT 1 FROM pair_leases WHERE command_id = NEW.command_id)
        THEN RAISE(ABORT, 'command_exists') END;

    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM sessions s JOIN session_state t ON t.session_id = s.id
        JOIN family_state f ON f.family = s.family
        WHERE s.id = NEW.session_id AND s.actor_id = NEW.actor_id AND t.version = NEW.session_version
    ) THEN RAISE(ABORT, 'session_stale') END;

    SELECT CASE WHEN NEW.expires_at <= NEW.now_ms OR NEW.expires_at > NEW.now_ms + 7200000
        THEN RAISE(ABORT, 'lease_expiry') END;

    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM sessions s JOIN family_state f ON f.family = s.family
        JOIN evaluation_outputs a ON a.evaluation_id = f.evaluation_id AND a.output_key = NEW.a_output
        JOIN evaluation_outputs b ON b.evaluation_id = f.evaluation_id AND b.output_key = NEW.b_output
        WHERE s.id = NEW.session_id AND a.case_key = NEW.case_key AND b.case_key = NEW.case_key
            AND a.system_id != b.system_id AND a.verdict = 'pass' AND b.verdict = 'pass'
            AND a.content_digest != b.content_digest
            AND NOT EXISTS (SELECT 1 FROM session_cases WHERE session_id = s.id AND case_key = NEW.case_key)
    ) THEN RAISE(ABORT, 'candidate_stale') END;

    SELECT CASE WHEN NEW.repeat_of IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM vote_receipts r JOIN eligible_claims e ON e.receipt_seq = r.seq
        WHERE r.seq = NEW.repeat_of AND r.actor_id = NEW.actor_id
            AND r.repeat_of IS NULL AND NEW.now_ms >= r.recorded_at + 86400000
            AND NEW.a_output = r.b_output AND NEW.b_output = r.a_output
    ) THEN RAISE(ABORT, 'repeat_ineligible') END;

    UPDATE pair_leases SET status = 'revoked' WHERE status = 'pending'
        AND (actor_id = NEW.actor_id AND family = (SELECT family FROM sessions WHERE id = NEW.session_id)
            OR claim_id = NEW.claim_id)
        AND (expires_at <= NEW.now_ms OR id NOT IN (SELECT id FROM compatible_leases));

    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM pair_leases WHERE status = 'pending' AND actor_id = NEW.actor_id
            AND family = (SELECT family FROM sessions WHERE id = NEW.session_id)
    ) THEN RAISE(ABORT, 'pending_exists') END;

    SELECT CASE WHEN (SELECT count(*) FROM pair_leases l
        JOIN actor_resolution a ON a.family = l.family AND a.actor_id = l.actor_id
        WHERE l.status = 'pending' AND l.expires_at > NEW.now_ms
            AND a.canonical_actor = (SELECT canonical_actor FROM actor_resolution
                WHERE actor_id = NEW.actor_id AND family = (SELECT family FROM sessions WHERE id = NEW.session_id))) >= 4
        THEN RAISE(ABORT, 'pending_limit') END;

    INSERT INTO claims(id, family, canonical_actor, kind, comparison_key, repeat_of)
    SELECT NEW.claim_id, s.family, a.canonical_actor,
        CASE WHEN NEW.repeat_of IS NULL THEN 'ordinary' ELSE 'repeat' END,
        NEW.comparison_key, NEW.repeat_of
    FROM sessions s JOIN actor_resolution a ON a.family = s.family AND a.actor_id = s.actor_id
    WHERE s.id = NEW.session_id AND NEW.claim_version = 0
        AND NOT EXISTS (SELECT 1 FROM claims WHERE id = NEW.claim_id);

    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM claims c JOIN sessions s ON s.family = c.family
        JOIN actor_resolution a ON a.family = s.family AND a.actor_id = s.actor_id
        WHERE c.id = NEW.claim_id AND s.id = NEW.session_id AND c.canonical_actor = a.canonical_actor
            AND c.version = NEW.claim_version AND c.merged_into IS NULL
            AND c.comparison_key = NEW.comparison_key AND c.repeat_of IS NEW.repeat_of
            AND NOT EXISTS (SELECT 1 FROM eligible_claims e WHERE e.claim_id = c.id)
            AND NOT (c.kind = 'repeat' AND EXISTS (
                SELECT 1 FROM receipt_state r WHERE r.receipt_seq = c.selected_receipt
                    AND r.withdrawn IS NULL AND r.superseded IS NULL))
    ) THEN RAISE(ABORT, 'claim_stale') END;

    INSERT INTO spend_quotas(operation, actor_id, family, now_ms, network)
    SELECT 'issue', NEW.actor_id, family, NEW.now_ms, NEW.network FROM sessions WHERE id = NEW.session_id;

    INSERT INTO pair_leases(id, command_id, request_hash, actor_id, family, session_id,
        claim_id, claim_version, evaluation_id, publication_id, point_id, case_key,
        a_output, b_output, a_system, b_system, a_fingerprint, b_fingerprint,
        issued_at, expires_at, repeat_of, status)
    SELECT NEW.lease_id, NEW.command_id, NEW.request_hash, NEW.actor_id, s.family, s.id,
        c.id, c.version + 1, f.evaluation_id, f.publication_id, f.point_id, NEW.case_key,
        a.output_key, b.output_key, a.system_id, b.system_id, sa.fingerprint, sb.fingerprint,
        NEW.now_ms, NEW.expires_at, NEW.repeat_of, 'pending'
    FROM sessions s JOIN family_state f ON f.family = s.family JOIN claims c ON c.id = NEW.claim_id
    JOIN evaluation_outputs a ON a.evaluation_id = f.evaluation_id AND a.output_key = NEW.a_output
    JOIN evaluation_outputs b ON b.evaluation_id = f.evaluation_id AND b.output_key = NEW.b_output
    JOIN evaluation_systems sa ON sa.evaluation_id = a.evaluation_id AND sa.system_id = a.system_id
    JOIN evaluation_systems sb ON sb.evaluation_id = b.evaluation_id AND sb.system_id = b.system_id
    WHERE s.id = NEW.session_id;

    UPDATE claims SET version = version + 1, current_lease = NEW.lease_id WHERE id = NEW.claim_id;
    UPDATE session_state SET version = version + 1, current_lease = NEW.lease_id WHERE session_id = NEW.session_id;
END;

CREATE VIEW accept_vote AS
SELECT NULL AS command_id, NULL AS request_hash, NULL AS actor_id, NULL AS lease_id,
    NULL AS acknowledgment, NULL AS choice, NULL AS defects, NULL AS now_ms,
    NULL AS network, NULL AS attribution WHERE 0;

CREATE TRIGGER accept_vote_insert INSTEAD OF INSERT ON accept_vote
BEGIN
    SELECT CASE WHEN EXISTS (SELECT 1 FROM pair_leases WHERE command_id = NEW.command_id)
        OR EXISTS (SELECT 1 FROM admission_results WHERE command_id = NEW.command_id)
        THEN RAISE(ABORT, 'conflict') END;
    SELECT CASE WHEN EXISTS (SELECT 1 FROM evidence_events WHERE command_id = NEW.command_id)
        THEN RAISE(ABORT, 'command_exists') END;

    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM compatible_leases l JOIN claims c ON c.id = l.claim_id
        JOIN session_state s ON s.session_id = l.session_id
        JOIN actor_resolution a ON a.actor_id = l.actor_id AND a.family = l.family
        WHERE l.id = NEW.lease_id AND l.actor_id = NEW.actor_id AND l.status = 'pending'
            AND l.expires_at > NEW.now_ms AND l.issued_at <= NEW.now_ms
            AND c.version = l.claim_version AND c.current_lease = l.id AND c.merged_into IS NULL
            AND c.canonical_actor = a.canonical_actor AND s.current_lease = l.id
    ) THEN RAISE(ABORT, 'lease_stale') END;

    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM pair_leases l WHERE l.id = NEW.lease_id AND l.repeat_of IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM eligible_claims WHERE receipt_seq = l.repeat_of)
    ) THEN RAISE(ABORT, 'repeat_ineligible') END;

    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM pair_leases l JOIN family_state f ON f.family = l.family
        JOIN evaluations e ON e.id = f.evaluation_id, json_each(NEW.defects) side, json_each(side.value) defect
        WHERE l.id = NEW.lease_id AND defect.value NOT IN (
            SELECT value FROM json_each(e.body, '$.manifest.defects'))
    ) THEN RAISE(ABORT, 'defects_invalid') END;

    SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.attribution) a
        WHERE (SELECT attribution_ms FROM deployment WHERE id = 1) = 0
            OR json_extract(a.value, '$.expires_at') != NEW.now_ms + (SELECT attribution_ms FROM deployment WHERE id = 1))
        THEN RAISE(ABORT, 'attribution_expiry') END;
    SELECT CASE WHEN (SELECT mode FROM deployment WHERE id = 1) = 'public' AND (
        (SELECT count(*) FROM json_each(NEW.attribution)) != 2
        OR (SELECT count(*) FROM json_each(NEW.attribution) WHERE json_extract(value, '$.kind') = 'exact') != 1
        OR (SELECT count(*) FROM json_each(NEW.attribution) WHERE json_extract(value, '$.kind') = 'prefix') != 1
        OR (SELECT count(DISTINCT json_extract(value, '$.version')) FROM json_each(NEW.attribution)) != 1)
        THEN RAISE(ABORT, 'attribution_context') END;

    INSERT INTO spend_quotas(operation, actor_id, family, now_ms, network)
    SELECT 'accept', NEW.actor_id, family, NEW.now_ms, NEW.network FROM pair_leases WHERE id = NEW.lease_id;

    INSERT INTO evidence_events(command_id, kind, family, principal, request_hash, created_at, data)
    SELECT NEW.command_id, 'accept', family, NEW.actor_id, NEW.request_hash, NEW.now_ms, '{}'
    FROM pair_leases WHERE id = NEW.lease_id;

    INSERT INTO vote_receipts(seq, acknowledgment, origin, lease_id, actor_id, session_id, family,
        evaluation_id, publication_id, point_id, claim_id, claim_version, case_key,
        a_output, b_output, a_system, b_system, a_fingerprint, b_fingerprint,
        choice, defects, duration_ms, repeat_of, recorded_at)
    SELECT e.seq, NEW.acknowledgment, 'served', l.id, l.actor_id, l.session_id, l.family,
        l.evaluation_id, l.publication_id, l.point_id, l.claim_id, l.claim_version, l.case_key,
        l.a_output, l.b_output, l.a_system, l.b_system, l.a_fingerprint, l.b_fingerprint,
        NEW.choice, NEW.defects, NEW.now_ms - l.issued_at, l.repeat_of, NEW.now_ms
    FROM pair_leases l JOIN evidence_events e ON e.command_id = NEW.command_id WHERE l.id = NEW.lease_id;

    INSERT INTO event_targets(event_seq, ordinal, receipt_seq, claim_id, expected_version, transition)
    SELECT e.seq, 0, c.selected_receipt, c.id, c.version, 'supersede'
    FROM pair_leases l JOIN claims c ON c.id = l.claim_id JOIN evidence_events e ON e.command_id = NEW.command_id
    WHERE l.id = NEW.lease_id AND c.selected_receipt IS NOT NULL;

    UPDATE receipt_state SET superseded = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id)
    WHERE receipt_seq = (SELECT c.selected_receipt FROM claims c JOIN pair_leases l ON l.claim_id = c.id WHERE l.id = NEW.lease_id);

    INSERT INTO receipt_state(receipt_seq) SELECT seq FROM evidence_events WHERE command_id = NEW.command_id;
    UPDATE pair_leases SET status = 'accepted', receipt_seq = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id)
    WHERE id = NEW.lease_id;

    UPDATE claims SET selected_receipt = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id),
        version = version + 1, current_lease = NULL WHERE id = (SELECT claim_id FROM pair_leases WHERE id = NEW.lease_id);
    UPDATE session_state SET version = version + 1, current_lease = NULL
    WHERE session_id = (SELECT session_id FROM pair_leases WHERE id = NEW.lease_id);
    INSERT INTO session_cases(session_id, case_key, receipt_seq)
    SELECT session_id, case_key, receipt_seq FROM pair_leases WHERE id = NEW.lease_id;

    INSERT INTO refresh_coverage(claim_id) SELECT claim_id FROM pair_leases WHERE id = NEW.lease_id;
    INSERT INTO network_attribution(receipt_seq, key_version, tag_kind, tag, created_at, expires_at)
    SELECT e.seq, json_extract(a.value, '$.version'), json_extract(a.value, '$.kind'),
        json_extract(a.value, '$.tag'), NEW.now_ms, json_extract(a.value, '$.expires_at')
    FROM json_each(NEW.attribution) a JOIN evidence_events e ON e.command_id = NEW.command_id;

    UPDATE family_state SET last_evidence_seq = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id),
        revision = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id)
    WHERE family = (SELECT family FROM pair_leases WHERE id = NEW.lease_id);
END;

CREATE VIEW withdraw_latest AS
SELECT NULL AS command_id, NULL AS request_hash, NULL AS actor_id, NULL AS session_id,
    NULL AS session_version, NULL AS receipt_seq, NULL AS now_ms WHERE 0;

CREATE TRIGGER withdraw_latest_insert INSTEAD OF INSERT ON withdraw_latest
BEGIN
    SELECT CASE WHEN EXISTS (SELECT 1 FROM pair_leases WHERE command_id = NEW.command_id)
        OR EXISTS (SELECT 1 FROM admission_results WHERE command_id = NEW.command_id)
        THEN RAISE(ABORT, 'conflict') END;
    SELECT CASE WHEN EXISTS (SELECT 1 FROM evidence_events WHERE command_id = NEW.command_id)
        THEN RAISE(ABORT, 'command_exists') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM sessions s JOIN session_state t ON t.session_id = s.id
        WHERE s.id = NEW.session_id AND s.actor_id = NEW.actor_id AND t.version = NEW.session_version
            AND NEW.receipt_seq = (SELECT max(r.seq) FROM vote_receipts r
                JOIN receipt_state x ON x.receipt_seq = r.seq
                WHERE r.session_id = s.id AND x.withdrawn IS NULL)
    ) THEN RAISE(ABORT, 'session_stale') END;

    INSERT INTO evidence_events(command_id, kind, family, principal, request_hash, created_at, data)
    SELECT NEW.command_id, 'withdraw', family, NEW.actor_id, NEW.request_hash, NEW.now_ms, '{}'
    FROM sessions WHERE id = NEW.session_id;
    INSERT INTO event_targets(event_seq, ordinal, receipt_seq, claim_id, expected_version, transition)
    SELECT e.seq, 0, r.seq, c.id, c.version, 'withdraw'
    FROM vote_receipts r JOIN claims c ON c.id = r.claim_id JOIN evidence_events e ON e.command_id = NEW.command_id
    WHERE r.seq = NEW.receipt_seq;

    UPDATE receipt_state SET withdrawn = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id)
    WHERE receipt_seq = NEW.receipt_seq;
    UPDATE pair_leases SET status = 'revoked' WHERE status = 'pending' AND (session_id = NEW.session_id
        OR claim_id = (SELECT claim_id FROM vote_receipts WHERE seq = NEW.receipt_seq) OR repeat_of = NEW.receipt_seq);
    UPDATE claims SET version = version + 1, current_lease = NULL,
        selected_receipt = CASE WHEN selected_receipt = NEW.receipt_seq THEN NULL ELSE selected_receipt END
    WHERE id = (SELECT claim_id FROM vote_receipts WHERE seq = NEW.receipt_seq)
        OR current_lease IN (SELECT id FROM pair_leases WHERE status = 'revoked' AND (session_id = NEW.session_id OR repeat_of = NEW.receipt_seq));
    UPDATE session_state SET version = version + 1, current_lease = NULL
    WHERE session_id = NEW.session_id OR current_lease IN (SELECT id FROM pair_leases WHERE status = 'revoked' AND repeat_of = NEW.receipt_seq);
    DELETE FROM session_cases WHERE session_id = NEW.session_id AND receipt_seq = NEW.receipt_seq;
    INSERT INTO refresh_coverage(claim_id) SELECT claim_id FROM vote_receipts WHERE seq = NEW.receipt_seq;
    UPDATE family_state SET last_evidence_seq = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id),
        revision = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id)
    WHERE family = (SELECT family FROM sessions WHERE id = NEW.session_id);
END;
