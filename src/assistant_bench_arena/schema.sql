CREATE TABLE deployment (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    namespace TEXT NOT NULL UNIQUE,
    quota_body TEXT NOT NULL CHECK (json_valid(quota_body) AND json_type(quota_body) = 'array'),
    attribution_ms INTEGER NOT NULL CHECK (attribution_ms BETWEEN 1 AND 5184000000),
    schema_id TEXT NOT NULL CHECK (schema_id = 'arena-v1')
) STRICT;

CREATE TABLE quota_policies (
    deployment_id INTEGER NOT NULL DEFAULT 1 CHECK (deployment_id = 1) REFERENCES deployment(id),
    operation TEXT NOT NULL CHECK (operation IN ('admit', 'renew', 'issue', 'accept')),
    scope TEXT NOT NULL CHECK (scope IN ('actor', 'exact', 'prefix', 'deployment')),
    window_ms INTEGER NOT NULL CHECK (window_ms > 0),
    allowance INTEGER NOT NULL CHECK (allowance > 0),
    PRIMARY KEY (operation, scope, window_ms)
) STRICT;

CREATE TABLE policies (
    deployment_id INTEGER NOT NULL DEFAULT 1 CHECK (deployment_id = 1) REFERENCES deployment(id),
    id TEXT PRIMARY KEY,
    body TEXT NOT NULL CHECK (json_valid(body))
) STRICT;

CREATE TABLE evaluations (
    id TEXT PRIMARY KEY,
    family TEXT NOT NULL,
    policy_id TEXT NOT NULL REFERENCES policies (id),
    body TEXT NOT NULL CHECK (json_valid(body)),
    case_count INTEGER NOT NULL CHECK (case_count >= 0),
    system_count INTEGER NOT NULL CHECK (system_count >= 0),
    output_count INTEGER NOT NULL CHECK (output_count >= 0),
    numerical_count INTEGER NOT NULL CHECK (numerical_count >= 0),
    sealed_at INTEGER CHECK (sealed_at >= 0)
) STRICT;

CREATE TABLE evaluation_cases (
    evaluation_id TEXT NOT NULL REFERENCES evaluations (id),
    case_key TEXT NOT NULL,
    category TEXT,
    source TEXT NOT NULL CHECK (source IN ('public', 'synthetic', 'private')),
    instruction_digest TEXT NOT NULL,
    body TEXT NOT NULL CHECK (json_valid(body)),
    PRIMARY KEY (evaluation_id, case_key)
) STRICT;

CREATE TABLE evaluation_systems (
    evaluation_id TEXT NOT NULL REFERENCES evaluations (id),
    system_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    declared_order INTEGER NOT NULL CHECK (declared_order >= 0),
    body TEXT NOT NULL CHECK (json_valid(body)),
    PRIMARY KEY (evaluation_id, system_id),
    UNIQUE (evaluation_id, declared_order)
) STRICT;

CREATE TABLE evaluation_outputs (
    evaluation_id TEXT NOT NULL,
    output_key TEXT NOT NULL,
    case_key TEXT NOT NULL,
    system_id TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('pass', 'fail', 'ungraded')),
    output_length INTEGER NOT NULL CHECK (output_length >= 0),
    approved_public INTEGER NOT NULL CHECK (approved_public IN (0, 1)),
    body TEXT NOT NULL CHECK (json_valid(body)),
    PRIMARY KEY (evaluation_id, output_key),
    FOREIGN KEY (evaluation_id, case_key)
        REFERENCES evaluation_cases (evaluation_id, case_key),
    FOREIGN KEY (evaluation_id, system_id)
        REFERENCES evaluation_systems (evaluation_id, system_id)
) STRICT;

CREATE TABLE evaluation_numerics (
    evaluation_id TEXT NOT NULL REFERENCES evaluations (id),
    section TEXT NOT NULL,
    row_key TEXT NOT NULL,
    body TEXT NOT NULL CHECK (json_valid(body)),
    PRIMARY KEY (evaluation_id, section, row_key)
) STRICT;

CREATE TABLE publications (
    id TEXT PRIMARY KEY,
    evaluation_id TEXT NOT NULL REFERENCES evaluations (id),
    body TEXT NOT NULL CHECK (json_valid(body)),
    output_count INTEGER NOT NULL CHECK (output_count >= 0),
    sealed_at INTEGER CHECK (sealed_at >= 0)
) STRICT;

CREATE TABLE rendered_outputs (
    publication_id TEXT NOT NULL REFERENCES publications (id),
    output_key TEXT NOT NULL,
    render TEXT NOT NULL CHECK (render IN ('text', 'html', 'diff')),
    content TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    approved_public INTEGER NOT NULL CHECK (approved_public IN (0, 1)),
    body TEXT NOT NULL CHECK (json_valid(body)),
    PRIMARY KEY (publication_id, output_key)
) STRICT;

CREATE TABLE point_snapshots (
    id TEXT PRIMARY KEY,
    evaluation_id TEXT NOT NULL REFERENCES evaluations (id),
    policy_id TEXT NOT NULL REFERENCES policies (id),
    source_export_id TEXT,
    body TEXT NOT NULL CHECK (json_valid(body)),
    sealed_at INTEGER NOT NULL CHECK (sealed_at >= 0)
) STRICT;

CREATE TABLE family_state (
    family TEXT PRIMARY KEY,
    evaluation_id TEXT NOT NULL REFERENCES evaluations (id),
    publication_id TEXT NOT NULL REFERENCES publications (id),
    point_id TEXT NOT NULL REFERENCES point_snapshots (id),
    policy_id TEXT NOT NULL REFERENCES policies (id),
    last_evidence_seq INTEGER NOT NULL DEFAULT 0 CHECK (last_evidence_seq >= 0),
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0)
) STRICT;

CREATE TABLE actors (
    deployment_id INTEGER NOT NULL DEFAULT 1 CHECK (deployment_id = 1) REFERENCES deployment(id),
    id TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    rate_subject TEXT UNIQUE
) STRICT;

CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL REFERENCES actors(id),
    family TEXT NOT NULL,
    created_at INTEGER NOT NULL CHECK (created_at >= 0)
) STRICT;

CREATE TABLE session_state (
    session_id TEXT PRIMARY KEY REFERENCES sessions(id),
    version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
    current_lease TEXT
) STRICT;

CREATE TABLE evidence_events (
    deployment_id INTEGER NOT NULL DEFAULT 1 CHECK (deployment_id = 1) REFERENCES deployment(id),
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    command_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('activate', 'accept', 'withdraw', 'exclude', 'restore')),
    family TEXT NOT NULL,
    principal TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    data TEXT NOT NULL CHECK (json_valid(data))
) STRICT;

CREATE TABLE claims (
    id TEXT PRIMARY KEY,
    family TEXT NOT NULL,
    actor_id TEXT NOT NULL REFERENCES actors(id),
    kind TEXT NOT NULL CHECK (kind IN ('ordinary', 'repeat')),
    comparison_key TEXT NOT NULL,
    repeat_of INTEGER,
    version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
    selected_receipt INTEGER,
    current_lease TEXT,
    CHECK ((kind = 'repeat') = (repeat_of IS NOT NULL))
) STRICT;

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
    recorded_at INTEGER NOT NULL CHECK (recorded_at >= 0)
) STRICT;

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
    claim_id TEXT,
    expected_version INTEGER CHECK (expected_version >= 0),
    transition TEXT NOT NULL,
    reference_seq INTEGER REFERENCES evidence_events(seq),
    lease_id TEXT,
    PRIMARY KEY (event_seq, ordinal)
) STRICT;

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

CREATE TABLE network_attribution (
    receipt_seq INTEGER NOT NULL REFERENCES vote_receipts(seq),
    key_version TEXT NOT NULL,
    tag_kind TEXT NOT NULL CHECK (tag_kind IN ('exact', 'prefix')),
    tag TEXT NOT NULL,
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    expires_at INTEGER NOT NULL CHECK (expires_at > created_at),
    PRIMARY KEY (receipt_seq, key_version, tag_kind)
) STRICT;

CREATE TABLE moderation_batches (
    deployment_id INTEGER NOT NULL DEFAULT 1 CHECK (deployment_id = 1) REFERENCES deployment(id),
    id TEXT PRIMARY KEY,
    family TEXT NOT NULL,
    principal TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('exclude', 'restore')),
    reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 500),
    target_count INTEGER NOT NULL CHECK (target_count > 0),
    target_digest TEXT NOT NULL,
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    sealed_at INTEGER CHECK (sealed_at >= created_at)
) STRICT;

CREATE TABLE moderation_targets (
    batch_id TEXT NOT NULL REFERENCES moderation_batches(id),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    receipt_seq INTEGER NOT NULL REFERENCES vote_receipts(seq),
    claim_version INTEGER NOT NULL CHECK (claim_version >= 0),
    exclusion_seq INTEGER REFERENCES evidence_events(seq),
    PRIMARY KEY (batch_id, ordinal),
    UNIQUE (batch_id, receipt_seq)
) STRICT;

CREATE TABLE moderation_applications (
    batch_id TEXT NOT NULL REFERENCES moderation_batches(id),
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    event_seq INTEGER NOT NULL UNIQUE REFERENCES evidence_events(seq),
    PRIMARY KEY (batch_id, chunk_index)
) STRICT;

CREATE TABLE moderation_progress (
    batch_id TEXT NOT NULL REFERENCES moderation_batches(id),
    claim_id TEXT NOT NULL REFERENCES claims(id),
    expected_version INTEGER NOT NULL CHECK (expected_version >= 0),
    PRIMARY KEY (batch_id, claim_id)
) STRICT;

CREATE TABLE analysis_exports (
    id TEXT PRIMARY KEY,
    family TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    namespace TEXT NOT NULL,
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    event_cutoff INTEGER NOT NULL CHECK (event_cutoff >= 0),
    receipt_cutoff INTEGER NOT NULL CHECK (receipt_cutoff >= 0),
    family_revision INTEGER NOT NULL CHECK (family_revision >= 0),
    analysis_sequence INTEGER NOT NULL CHECK (analysis_sequence >= 0),
    evaluation_id TEXT NOT NULL REFERENCES evaluations(id),
    publication_id TEXT NOT NULL REFERENCES publications(id),
    point_id TEXT NOT NULL REFERENCES point_snapshots(id),
    policy_id TEXT NOT NULL REFERENCES policies(id),
    policy TEXT NOT NULL CHECK (json_valid(policy))
) STRICT;

CREATE TABLE lease_revocations (
    id TEXT PRIMARY KEY REFERENCES pair_leases(id)
) STRICT;

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

CREATE INDEX session_current_lease ON session_state(current_lease);

CREATE INDEX evidence_events_family ON evidence_events(family, seq);

CREATE UNIQUE INDEX claims_current ON claims(family, actor_id, kind, comparison_key);

CREATE INDEX claims_selection ON claims(selected_receipt);

CREATE INDEX claims_current_lease ON claims(current_lease);

CREATE UNIQUE INDEX leases_actor_pending ON pair_leases(actor_id, family) WHERE status = 'pending';

CREATE UNIQUE INDEX leases_claim_pending ON pair_leases(claim_id) WHERE status = 'pending';

CREATE INDEX leases_repeat ON pair_leases(repeat_of) WHERE status = 'pending';

CREATE INDEX leases_family_status ON pair_leases(family, status);

CREATE INDEX leases_expiry ON pair_leases(expires_at, id);

CREATE INDEX receipts_session ON vote_receipts(session_id, seq DESC);

CREATE INDEX receipts_family ON vote_receipts(family, seq);

CREATE INDEX receipts_actor ON vote_receipts(actor_id, claim_id);

CREATE INDEX targets_receipt ON event_targets(receipt_seq, event_seq);

CREATE INDEX quota_expiry ON quota_windows(expires_at);

CREATE INDEX credential_expiry ON credentials(expires_at);

CREATE INDEX admission_expiry ON admission_results(expires_at);

CREATE INDEX attribution_tag ON network_attribution(key_version, tag_kind, tag, receipt_seq);

CREATE INDEX attribution_expiry ON network_attribution(expires_at);

CREATE INDEX numerics_page ON evaluation_numerics(evaluation_id, json_array(section, row_key));

CREATE INDEX receipts_repeat_due ON vote_receipts(actor_id, family, seq, recorded_at)
WHERE repeat_of IS NULL;

CREATE INDEX claims_actor_page ON claims(family, actor_id, id);

CREATE INDEX admission_original_credential ON admission_results(original_credential);

CREATE INDEX leases_pending_expiry ON pair_leases(expires_at, id) WHERE status = 'pending';

CREATE INDEX sessions_created ON sessions(created_at, id);

CREATE INDEX actors_created ON actors(created_at, id);

CREATE INDEX actor_expiry_due ON actor_expiry(expires_at, actor_id);

CREATE INDEX session_expiry_due ON session_expiry(expires_at, session_id);

CREATE INDEX claim_expiry_due ON claim_expiry(expires_at, claim_id);

CREATE INDEX events_principal ON evidence_events(principal);

CREATE INDEX sessions_actor ON sessions(actor_id);

CREATE INDEX credentials_actor ON credentials(actor_id);

CREATE INDEX admissions_actor ON admission_results(actor_id);

CREATE INDEX claims_actor_id ON claims(actor_id);

CREATE VIEW seal_evaluation AS
SELECT NULL AS evaluation_id, NULL AS now_ms WHERE 0;

CREATE VIEW seal_publication AS
SELECT NULL AS publication_id, NULL AS now_ms WHERE 0;

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
WHERE c.kind = 'ordinary'
    AND s.withdrawn IS NULL AND s.excluded IS NULL AND s.superseded IS NULL
    AND a.case_key = r.case_key AND b.case_key = r.case_key
    AND a.system_id = r.a_system AND b.system_id = r.b_system
    AND sa.fingerprint = r.a_fingerprint AND sb.fingerprint = r.b_fingerprint
    AND a.verdict = 'pass' AND b.verdict = 'pass' AND a.content_digest != b.content_digest;

CREATE VIEW refresh_coverage AS SELECT NULL AS claim_id WHERE 0;

CREATE VIEW spend_quotas AS
SELECT NULL AS operation, NULL AS actor_id, NULL AS family, NULL AS now_ms,
    NULL AS network WHERE 0;

CREATE VIEW admit_credential AS
SELECT NULL AS command_id, NULL AS request_hash, NULL AS operation,
    NULL AS context_digest, NULL AS proof_digest, NULL AS original_credential,
    NULL AS actor_id, NULL AS credential_id, NULL AS rate_subject, NULL AS now_ms,
    NULL AS expires_at, NULL AS challenge_until, NULL AS network WHERE 0;

CREATE VIEW issue_pair AS
SELECT NULL AS command_id, NULL AS request_hash, NULL AS actor_id, NULL AS lease_id,
    NULL AS session_id, NULL AS session_version, NULL AS claim_id, NULL AS claim_version,
    NULL AS case_key, NULL AS a_output, NULL AS b_output, NULL AS comparison_key,
    NULL AS repeat_of, NULL AS now_ms, NULL AS expires_at, NULL AS network WHERE 0;

CREATE VIEW accept_vote AS
SELECT NULL AS command_id, NULL AS request_hash, NULL AS actor_id, NULL AS lease_id,
    NULL AS acknowledgment, NULL AS choice, NULL AS defects, NULL AS now_ms,
    NULL AS network, NULL AS attribution WHERE 0;

CREATE VIEW withdraw_latest AS
SELECT NULL AS command_id, NULL AS request_hash, NULL AS actor_id, NULL AS session_id,
    NULL AS session_version, NULL AS receipt_seq, NULL AS now_ms WHERE 0;

CREATE VIEW activate_family AS
SELECT NULL AS command_id, NULL AS request_hash, NULL AS principal, NULL AS now_ms,
    NULL AS family, NULL AS revision, NULL AS evaluation_id, NULL AS publication_id,
    NULL AS point_id WHERE 0;

CREATE VIEW seal_moderation AS SELECT NULL AS batch_id, NULL AS now_ms WHERE 0;

CREATE VIEW moderate_chunk AS
SELECT NULL AS command_id, NULL AS request_hash, NULL AS principal, NULL AS now_ms,
    NULL AS batch_id, NULL AS chunk_index WHERE 0;

CREATE VIEW freeze_export AS SELECT NULL AS id, NULL AS family, NULL AS now_ms WHERE 0;

CREATE VIEW export_evaluation(export_id, section, row_key, body) AS
SELECT x.id AS export_id, 'evaluation' AS section, e.id AS row_key, e.body
FROM analysis_exports x JOIN evaluations e ON e.id = x.evaluation_id;

CREATE VIEW export_publication(export_id, section, row_key, body) AS
SELECT x.id, 'publication', p.id, p.body
FROM analysis_exports x JOIN publications p ON p.id = x.publication_id;

CREATE VIEW export_points(export_id, section, row_key, body) AS
SELECT x.id, 'points', p.id, p.body
FROM analysis_exports x JOIN point_snapshots p ON p.id = x.point_id;

CREATE VIEW export_policy(export_id, section, row_key, body) AS
SELECT x.id, 'policy', p.id, p.body
FROM analysis_exports x JOIN policies p ON p.id = x.policy_id;

CREATE VIEW export_cases(export_id, section, row_key, body) AS
SELECT x.id, 'cases', c.case_key, c.body
FROM analysis_exports x JOIN evaluation_cases c ON c.evaluation_id = x.evaluation_id;

CREATE VIEW export_systems(export_id, section, row_key, body) AS
SELECT x.id, 'systems', s.system_id, s.body
FROM analysis_exports x JOIN evaluation_systems s ON s.evaluation_id = x.evaluation_id;

CREATE VIEW export_outputs(export_id, section, row_key, body) AS
SELECT x.id, 'outputs', o.output_key, o.body
FROM analysis_exports x JOIN evaluation_outputs o ON o.evaluation_id = x.evaluation_id;

CREATE VIEW export_numerics(export_id, section, row_key, body) AS
SELECT x.id, 'numerics', json_array(n.section, n.row_key), n.body
FROM analysis_exports x JOIN evaluation_numerics n ON n.evaluation_id = x.evaluation_id;

CREATE VIEW observation_bodies AS
SELECT r.seq, json_object(
    'seq', r.seq, 'acknowledgment', r.acknowledgment,
    'actor_id', r.actor_id, 'session_id', r.session_id, 'family', r.family,
    'claim_id', r.claim_id, 'lease_id', r.lease_id,
    'evaluation_id', r.evaluation_id, 'publication_id', r.publication_id,
    'point_id', r.point_id, 'claim_version', r.claim_version,
    'case_key', r.case_key, 'a_output', r.a_output, 'b_output', r.b_output,
    'a_system', r.a_system, 'b_system', r.b_system,
    'a_fingerprint', r.a_fingerprint, 'b_fingerprint', r.b_fingerprint,
    'choice', r.choice, 'defects', json(r.defects), 'repeat_of', r.repeat_of,
    'recorded_at', r.recorded_at, 'duration_ms', r.duration_ms) AS body
FROM vote_receipts r;

CREATE VIEW export_receipts(export_id, section, row_key, body) AS
SELECT x.id, 'receipts', printf('%020d', r.seq), o.body
FROM analysis_exports x JOIN vote_receipts r ON r.family = x.family AND r.seq <= x.receipt_cutoff
JOIN observation_bodies o ON o.seq = r.seq;

CREATE VIEW export_events(export_id, section, row_key, body) AS
SELECT x.id, 'events', printf('%020d', e.seq), json_object(
    'seq', e.seq, 'command_id', e.command_id, 'kind', e.kind, 'family', e.family,
    'principal', e.principal, 'request_hash', e.request_hash,
    'created_at', e.created_at, 'data', json(e.data))
FROM analysis_exports x JOIN evidence_events e ON e.family = x.family AND e.seq <= x.event_cutoff;

CREATE VIEW export_targets(export_id, section, row_key, body) AS
SELECT x.id, 'targets', printf('%020d/%020d', t.event_seq, t.ordinal), json_object(
    'event_seq', t.event_seq, 'ordinal', t.ordinal, 'receipt_seq', t.receipt_seq,
    'claim_id', t.claim_id, 'expected_version', t.expected_version,
    'transition', t.transition, 'reference_seq', t.reference_seq, 'lease_id', t.lease_id)
FROM analysis_exports x JOIN evidence_events e ON e.family = x.family AND e.seq <= x.event_cutoff
JOIN event_targets t ON t.event_seq = e.seq;

CREATE VIEW export_actors(export_id, section, row_key, body) AS
SELECT x.id, 'actors', a.id, json_object('id', a.id, 'created_at', a.created_at)
FROM analysis_exports x JOIN actors a ON a.id IN (
    SELECT actor_id FROM vote_receipts WHERE family = x.family AND seq <= x.receipt_cutoff);

CREATE VIEW export_sessions AS
SELECT x.id AS export_id, 'sessions' AS section, s.id AS row_key,
    json_object('id', s.id, 'actor_id', s.actor_id,
        'family', s.family, 'created_at', s.created_at) AS body
FROM analysis_exports x JOIN sessions s ON s.id IN (
    SELECT session_id FROM vote_receipts
    WHERE family = x.family AND seq <= x.receipt_cutoff);

CREATE VIEW expire_leases AS SELECT NULL AS now_ms WHERE 0;

CREATE TRIGGER deployment_initialize AFTER INSERT ON deployment
BEGIN
    INSERT INTO quota_policies(operation, scope, window_ms, allowance)
    SELECT json_extract(value, '$.operation'), json_extract(value, '$.scope'),
        json_extract(value, '$.window_ms'), json_extract(value, '$.limit')
    FROM json_each(NEW.quota_body);
END;

CREATE TRIGGER seal_evaluation_insert INSTEAD OF INSERT ON seal_evaluation
WHEN (SELECT sealed_at FROM evaluations WHERE id = NEW.evaluation_id) IS NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM evaluations e
        WHERE e.id = NEW.evaluation_id AND e.sealed_at IS NULL
            AND e.case_count = (
                SELECT COUNT(*) FROM evaluation_cases WHERE evaluation_id = e.id
            )
            AND e.system_count = (
                SELECT COUNT(*) FROM evaluation_systems WHERE evaluation_id = e.id
            )
            AND e.output_count = (
                SELECT COUNT(*) FROM evaluation_outputs WHERE evaluation_id = e.id
            )
            AND e.numerical_count = (
                SELECT COUNT(*) FROM evaluation_numerics WHERE evaluation_id = e.id
            )
    ) THEN RAISE(ABORT, 'invalid_release') END;

    SELECT CASE WHEN (
        EXISTS (SELECT 1 FROM evaluation_cases
                WHERE evaluation_id = NEW.evaluation_id AND source = 'private')
        OR EXISTS (SELECT 1 FROM evaluation_outputs
                   WHERE evaluation_id = NEW.evaluation_id AND approved_public = 0)
    ) THEN RAISE(ABORT, 'private_material') END;

    UPDATE evaluations SET sealed_at = NEW.now_ms WHERE id = NEW.evaluation_id;
END;

CREATE TRIGGER seal_publication_insert INSTEAD OF INSERT ON seal_publication
WHEN (SELECT sealed_at FROM publications WHERE id = NEW.publication_id) IS NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM publications p JOIN evaluations e ON e.id = p.evaluation_id
        WHERE p.id = NEW.publication_id AND p.sealed_at IS NULL
            AND e.sealed_at IS NOT NULL
            AND p.output_count = (
                SELECT COUNT(*) FROM rendered_outputs WHERE publication_id = p.id
            )
            AND NOT EXISTS (
                SELECT 1 FROM rendered_outputs r
                LEFT JOIN evaluation_outputs o ON o.evaluation_id = e.id
                    AND o.output_key = r.output_key
                WHERE r.publication_id = p.id
                    AND (o.output_key IS NULL OR o.content_digest != r.content_digest)
            )
    ) THEN RAISE(ABORT, 'invalid_release') END;

    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM rendered_outputs
        WHERE publication_id = NEW.publication_id AND approved_public = 0
    ) THEN RAISE(ABORT, 'private_material') END;

    UPDATE publications SET sealed_at = NEW.now_ms WHERE id = NEW.publication_id;
END;

CREATE TRIGGER evaluations_immutable BEFORE UPDATE ON evaluations
WHEN OLD.sealed_at IS NOT NULL OR NEW.id != OLD.id OR NEW.family != OLD.family
    OR NEW.policy_id != OLD.policy_id OR NEW.body != OLD.body
    OR NEW.case_count != OLD.case_count OR NEW.system_count != OLD.system_count
    OR NEW.output_count != OLD.output_count OR NEW.numerical_count != OLD.numerical_count
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER evaluations_no_delete BEFORE DELETE ON evaluations
WHEN OLD.sealed_at IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER evaluations_no_replace BEFORE INSERT ON evaluations
WHEN EXISTS (SELECT 1 FROM evaluations WHERE id = NEW.id)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER publications_immutable BEFORE UPDATE ON publications
WHEN OLD.sealed_at IS NOT NULL OR NEW.id != OLD.id
    OR NEW.evaluation_id != OLD.evaluation_id OR NEW.body != OLD.body
    OR NEW.output_count != OLD.output_count
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER publications_no_delete BEFORE DELETE ON publications
WHEN OLD.sealed_at IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER publications_no_replace BEFORE INSERT ON publications
WHEN EXISTS (SELECT 1 FROM publications WHERE id = NEW.id)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER cases_no_insert BEFORE INSERT ON evaluation_cases
WHEN EXISTS (SELECT 1 FROM evaluation_cases WHERE evaluation_id = NEW.evaluation_id AND case_key = NEW.case_key)
    OR EXISTS (SELECT 1 FROM evaluations
             WHERE id = NEW.evaluation_id AND sealed_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER cases_no_update BEFORE UPDATE ON evaluation_cases
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER cases_no_delete BEFORE DELETE ON evaluation_cases
WHEN EXISTS (SELECT 1 FROM evaluations
             WHERE id = OLD.evaluation_id AND sealed_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER systems_no_insert BEFORE INSERT ON evaluation_systems
WHEN EXISTS (SELECT 1 FROM evaluation_systems WHERE evaluation_id = NEW.evaluation_id
    AND (system_id = NEW.system_id OR declared_order = NEW.declared_order))
    OR EXISTS (SELECT 1 FROM evaluations
             WHERE id = NEW.evaluation_id AND sealed_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER systems_no_update BEFORE UPDATE ON evaluation_systems
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER systems_no_delete BEFORE DELETE ON evaluation_systems
WHEN EXISTS (SELECT 1 FROM evaluations
             WHERE id = OLD.evaluation_id AND sealed_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER outputs_no_insert BEFORE INSERT ON evaluation_outputs
WHEN EXISTS (SELECT 1 FROM evaluation_outputs WHERE evaluation_id = NEW.evaluation_id AND output_key = NEW.output_key)
    OR EXISTS (SELECT 1 FROM evaluations
             WHERE id = NEW.evaluation_id AND sealed_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER outputs_no_update BEFORE UPDATE ON evaluation_outputs
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER outputs_no_delete BEFORE DELETE ON evaluation_outputs
WHEN EXISTS (SELECT 1 FROM evaluations
             WHERE id = OLD.evaluation_id AND sealed_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER numerics_no_insert BEFORE INSERT ON evaluation_numerics
WHEN EXISTS (SELECT 1 FROM evaluation_numerics WHERE evaluation_id = NEW.evaluation_id AND section = NEW.section AND row_key = NEW.row_key)
    OR EXISTS (SELECT 1 FROM evaluations
             WHERE id = NEW.evaluation_id AND sealed_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER numerics_no_update BEFORE UPDATE ON evaluation_numerics
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER numerics_no_delete BEFORE DELETE ON evaluation_numerics
WHEN EXISTS (SELECT 1 FROM evaluations
             WHERE id = OLD.evaluation_id AND sealed_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER render_no_insert BEFORE INSERT ON rendered_outputs
WHEN EXISTS (SELECT 1 FROM rendered_outputs WHERE publication_id = NEW.publication_id AND output_key = NEW.output_key)
    OR EXISTS (SELECT 1 FROM publications
             WHERE id = NEW.publication_id AND sealed_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER render_no_update BEFORE UPDATE ON rendered_outputs
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER render_no_delete BEFORE DELETE ON rendered_outputs
WHEN EXISTS (SELECT 1 FROM publications
             WHERE id = OLD.publication_id AND sealed_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER points_no_update BEFORE UPDATE ON point_snapshots
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER points_no_delete BEFORE DELETE ON point_snapshots
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER points_no_replace BEFORE INSERT ON point_snapshots
WHEN EXISTS (SELECT 1 FROM point_snapshots WHERE id = NEW.id)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER policies_no_update BEFORE UPDATE ON policies
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER policies_no_delete BEFORE DELETE ON policies
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER policies_no_replace BEFORE INSERT ON policies
WHEN EXISTS (SELECT 1 FROM policies WHERE id = NEW.id)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER deployment_no_update BEFORE UPDATE ON deployment
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER deployment_no_delete BEFORE DELETE ON deployment
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER deployment_no_replace BEFORE INSERT ON deployment
WHEN EXISTS (SELECT 1 FROM deployment)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER quota_policies_no_update BEFORE UPDATE ON quota_policies
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER quota_policies_no_delete BEFORE DELETE ON quota_policies
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER quota_policies_no_replace BEFORE INSERT ON quota_policies
WHEN EXISTS (SELECT 1 FROM quota_policies WHERE operation = NEW.operation AND scope = NEW.scope AND window_ms = NEW.window_ms)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER quota_policies_configuration BEFORE INSERT ON quota_policies
WHEN NOT EXISTS (SELECT 1 FROM deployment d, json_each(d.quota_body) p
    WHERE json_extract(p.value, '$.operation') = NEW.operation
        AND json_extract(p.value, '$.scope') = NEW.scope
        AND json_extract(p.value, '$.window_ms') = NEW.window_ms
        AND json_extract(p.value, '$.limit') = NEW.allowance)
BEGIN SELECT RAISE(ABORT, 'quota_policy'); END;

CREATE TRIGGER sessions_initialize AFTER INSERT ON sessions
BEGIN
    INSERT INTO session_state(session_id) VALUES (NEW.id);
END;

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
WHEN EXISTS (SELECT 1 FROM vote_receipts WHERE seq = NEW.seq
    OR acknowledgment = NEW.acknowledgment OR lease_id = NEW.lease_id)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER actors_no_update BEFORE UPDATE ON actors
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER actors_no_replace BEFORE INSERT ON actors
WHEN EXISTS (SELECT 1 FROM actors WHERE id = NEW.id OR rate_subject = NEW.rate_subject)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER sessions_no_update BEFORE UPDATE ON sessions
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

CREATE TRIGGER spend_quotas_insert INSTEAD OF INSERT ON spend_quotas
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
                        WHEN 'actor' THEN q.subject = NEW.actor_id
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

CREATE TRIGGER admit_credential_insert INSTEAD OF INSERT ON admit_credential
BEGIN
    SELECT CASE WHEN EXISTS (SELECT 1 FROM evidence_events WHERE command_id = NEW.command_id)
        OR EXISTS (SELECT 1 FROM pair_leases WHERE command_id = NEW.command_id)
        THEN RAISE(ABORT, 'conflict') END;
    SELECT CASE WHEN EXISTS (SELECT 1 FROM admission_results WHERE command_id = NEW.command_id)
        THEN RAISE(ABORT, 'command_exists') END;
    SELECT CASE WHEN EXISTS (SELECT 1 FROM admission_results WHERE proof_digest = NEW.proof_digest)
        THEN RAISE(ABORT, 'proof_consumed') END;
    SELECT CASE WHEN NEW.expires_at <= NEW.now_ms OR NEW.expires_at > NEW.now_ms + 2592000000
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

    INSERT INTO lease_revocations(id)
    SELECT id FROM pair_leases WHERE status = 'pending'
        AND (actor_id = NEW.actor_id AND family = (SELECT family FROM sessions WHERE id = NEW.session_id)
            OR claim_id = NEW.claim_id)
        AND (expires_at <= NEW.now_ms OR id NOT IN (SELECT id FROM compatible_leases));

    UPDATE pair_leases SET status = 'revoked' WHERE id IN (SELECT id FROM lease_revocations);

    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM pair_leases WHERE status = 'pending' AND actor_id = NEW.actor_id
            AND family = (SELECT family FROM sessions WHERE id = NEW.session_id)
    ) THEN RAISE(ABORT, 'pending_exists') END;

    SELECT CASE WHEN (SELECT count(*) FROM pair_leases l
        WHERE l.status = 'pending' AND l.expires_at > NEW.now_ms
            AND l.actor_id = NEW.actor_id) >= 4
        THEN RAISE(ABORT, 'pending_limit') END;

    INSERT INTO claims(id, family, actor_id, kind, comparison_key, repeat_of)
    SELECT NEW.claim_id, s.family, s.actor_id,
        CASE WHEN NEW.repeat_of IS NULL THEN 'ordinary' ELSE 'repeat' END,
        NEW.comparison_key, NEW.repeat_of
    FROM sessions s
    WHERE s.id = NEW.session_id AND NEW.claim_version = 0
        AND NOT EXISTS (SELECT 1 FROM claims WHERE id = NEW.claim_id);

    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM claims c JOIN sessions s ON s.family = c.family
        WHERE c.id = NEW.claim_id AND s.id = NEW.session_id AND c.actor_id = s.actor_id
            AND c.version = NEW.claim_version
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

    UPDATE claims SET current_lease = NULL, version = version + 1
    WHERE id != NEW.claim_id AND current_lease IN (SELECT id FROM lease_revocations);
    UPDATE session_state SET current_lease = NULL, version = version + 1
    WHERE session_id != NEW.session_id AND current_lease IN (SELECT id FROM lease_revocations);
    DELETE FROM lease_revocations;

    UPDATE claims SET version = version + 1, current_lease = NEW.lease_id WHERE id = NEW.claim_id;
    UPDATE session_state SET version = version + 1, current_lease = NEW.lease_id WHERE session_id = NEW.session_id;
END;

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
        WHERE l.id = NEW.lease_id AND l.actor_id = NEW.actor_id AND l.status = 'pending'
            AND l.expires_at > NEW.now_ms AND l.issued_at <= NEW.now_ms
            AND c.version = l.claim_version AND c.current_lease = l.id
            AND c.actor_id = l.actor_id AND s.current_lease = l.id
    ) THEN RAISE(ABORT, 'lease_stale') END;

    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM pair_leases l WHERE l.id = NEW.lease_id AND l.repeat_of IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM eligible_claims WHERE receipt_seq = l.repeat_of)
    ) THEN RAISE(ABORT, 'repeat_ineligible') END;

    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM pair_leases l JOIN family_state f ON f.family = l.family
        JOIN evaluations e ON e.id = f.evaluation_id, json_each(NEW.defects) side, json_each(side.value) defect
        WHERE l.id = NEW.lease_id AND defect.value NOT IN (
            SELECT value FROM json_each(e.body, '$.manifest.family.defects'))
    ) THEN RAISE(ABORT, 'defects_invalid') END;

    SELECT CASE WHEN EXISTS (SELECT 1 FROM json_each(NEW.attribution) a
        WHERE json_extract(a.value, '$.expires_at') != NEW.now_ms + (SELECT attribution_ms FROM deployment WHERE id = 1))
        THEN RAISE(ABORT, 'attribution_expiry') END;
    SELECT CASE WHEN (
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

    INSERT INTO vote_receipts(seq, acknowledgment, lease_id, actor_id, session_id, family,
        evaluation_id, publication_id, point_id, claim_id, claim_version, case_key,
        a_output, b_output, a_system, b_system, a_fingerprint, b_fingerprint,
        choice, defects, duration_ms, repeat_of, recorded_at)
    SELECT e.seq, NEW.acknowledgment, l.id, l.actor_id, l.session_id, l.family,
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

    UPDATE family_state SET last_evidence_seq = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id)
    WHERE family = (SELECT family FROM pair_leases WHERE id = NEW.lease_id);
END;

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
    UPDATE family_state SET last_evidence_seq = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id)
    WHERE family = (SELECT family FROM sessions WHERE id = NEW.session_id);
END;

CREATE TRIGGER activate_family_insert INSTEAD OF INSERT ON activate_family
BEGIN
    SELECT CASE WHEN EXISTS (SELECT 1 FROM pair_leases WHERE command_id = NEW.command_id)
        OR EXISTS (SELECT 1 FROM admission_results WHERE command_id = NEW.command_id)
        THEN RAISE(ABORT, 'conflict') END;
    SELECT CASE WHEN EXISTS (SELECT 1 FROM evidence_events WHERE command_id = NEW.command_id)
        THEN RAISE(ABORT, 'command_exists') END;
    SELECT CASE WHEN COALESCE((SELECT revision FROM family_state WHERE family = NEW.family), 0) != NEW.revision
        THEN RAISE(ABORT, 'family_stale') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM evaluations e JOIN publications p ON p.evaluation_id = e.id
        JOIN point_snapshots s ON s.evaluation_id = e.id AND s.policy_id = e.policy_id
        WHERE e.id = NEW.evaluation_id AND e.family = NEW.family AND e.sealed_at IS NOT NULL
            AND p.id = NEW.publication_id AND p.sealed_at IS NOT NULL AND s.id = NEW.point_id
    ) THEN RAISE(ABORT, 'publication_unsealed') END;

    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM family_state f
        JOIN point_snapshots current ON current.id = f.point_id
        JOIN point_snapshots proposed ON proposed.id = NEW.point_id
        WHERE f.family = NEW.family AND f.evaluation_id = NEW.evaluation_id
            AND f.policy_id = proposed.policy_id AND current.source_export_id IS NOT NULL
            AND (proposed.source_export_id IS NULL OR NOT EXISTS (
                SELECT 1 FROM analysis_exports newer JOIN analysis_exports older
                    ON older.id = current.source_export_id
                WHERE newer.id = proposed.source_export_id
                    AND newer.event_cutoff >= older.event_cutoff
                    AND newer.receipt_cutoff >= older.receipt_cutoff
                    AND newer.analysis_sequence >= older.analysis_sequence))
    ) THEN RAISE(ABORT, 'point_downgrade') END;

    INSERT INTO evidence_events(command_id, kind, family, principal, request_hash, created_at, data)
    VALUES (NEW.command_id, 'activate', NEW.family, NEW.principal, NEW.request_hash, NEW.now_ms,
        json_object('evaluation_id', NEW.evaluation_id, 'publication_id', NEW.publication_id,
            'point_id', NEW.point_id,
            'previous_evaluation_id', (SELECT evaluation_id FROM family_state WHERE family = NEW.family)));
    INSERT INTO family_state(family, evaluation_id, publication_id, point_id, policy_id, last_evidence_seq, revision)
    SELECT NEW.family, NEW.evaluation_id, NEW.publication_id, NEW.point_id, policy_id,
        (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id),
        (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id)
    FROM evaluations WHERE id = NEW.evaluation_id
    ON CONFLICT(family) DO UPDATE SET
        last_evidence_seq = CASE WHEN family_state.evaluation_id != excluded.evaluation_id OR family_state.policy_id != excluded.policy_id
            THEN excluded.last_evidence_seq ELSE family_state.last_evidence_seq END,
        evaluation_id = excluded.evaluation_id,
        publication_id = excluded.publication_id, point_id = excluded.point_id,
        policy_id = excluded.policy_id, revision = excluded.revision;

    INSERT INTO event_targets(event_seq, ordinal, claim_id, expected_version, transition, lease_id)
    SELECT e.seq, row_number() OVER (ORDER BY l.id) - 1, c.id, c.version, 'revoke', l.id
    FROM pair_leases l JOIN claims c ON c.id = l.claim_id
    JOIN evidence_events e ON e.command_id = NEW.command_id
    WHERE l.family = NEW.family AND l.status = 'pending'
        AND json_extract(e.data, '$.previous_evaluation_id') IS NOT NEW.evaluation_id
        AND l.id NOT IN (SELECT id FROM compatible_leases WHERE family = NEW.family);
    UPDATE pair_leases SET status = 'revoked' WHERE id IN (SELECT lease_id FROM event_targets
        WHERE event_seq = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id));
    UPDATE claims SET version = version + 1, current_lease = NULL WHERE id IN (SELECT claim_id FROM event_targets
        WHERE event_seq = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id));
    UPDATE session_state SET version = version + 1, current_lease = NULL
    WHERE current_lease IN (SELECT lease_id FROM event_targets
        WHERE event_seq = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id));
    DELETE FROM coverage_members WHERE family = NEW.family AND (
        SELECT json_extract(data, '$.previous_evaluation_id') IS NOT NEW.evaluation_id
        FROM evidence_events WHERE command_id = NEW.command_id);
    INSERT INTO coverage_members(receipt_seq, claim_id, family, a_system, b_system)
    SELECT receipt_seq, claim_id, family, a_system, b_system FROM eligible_claims WHERE family = NEW.family
        AND (SELECT json_extract(data, '$.previous_evaluation_id') IS NOT NEW.evaluation_id
            FROM evidence_events WHERE command_id = NEW.command_id);
END;

CREATE TRIGGER moderation_targets_initialize AFTER INSERT ON moderation_targets
BEGIN
    SELECT CASE WHEN EXISTS (SELECT 1 FROM moderation_progress p
        JOIN vote_receipts r ON r.claim_id = p.claim_id
        WHERE p.batch_id = NEW.batch_id AND r.seq = NEW.receipt_seq
            AND p.expected_version != NEW.claim_version)
        THEN RAISE(ABORT, 'moderation_stale') END;
    INSERT INTO moderation_progress(batch_id, claim_id, expected_version)
    SELECT NEW.batch_id, r.claim_id, NEW.claim_version FROM vote_receipts r
    WHERE r.seq = NEW.receipt_seq AND NOT EXISTS (
        SELECT 1 FROM moderation_progress WHERE batch_id = NEW.batch_id AND claim_id = r.claim_id);
END;

CREATE TRIGGER seal_moderation_insert INSTEAD OF INSERT ON seal_moderation
WHEN (SELECT sealed_at FROM moderation_batches WHERE id = NEW.batch_id) IS NULL
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM moderation_batches b WHERE b.id = NEW.batch_id AND b.sealed_at IS NULL
            AND b.target_count = (SELECT count(*) FROM moderation_targets WHERE batch_id = b.id)
            AND (SELECT min(ordinal) FROM moderation_targets WHERE batch_id = b.id) = 0
            AND (SELECT max(ordinal) FROM moderation_targets WHERE batch_id = b.id) = b.target_count - 1
            AND NOT EXISTS (SELECT 1 FROM moderation_targets t JOIN vote_receipts r ON r.seq = t.receipt_seq
                WHERE t.batch_id = b.id AND r.family != b.family)
    ) THEN RAISE(ABORT, 'moderation_unsealed') END;
    UPDATE moderation_batches SET sealed_at = NEW.now_ms WHERE id = NEW.batch_id;
END;

CREATE TRIGGER moderation_batches_guard BEFORE UPDATE ON moderation_batches
WHEN OLD.sealed_at IS NOT NULL OR NEW.sealed_at IS NULL OR OLD.id != NEW.id
    OR OLD.family != NEW.family OR OLD.principal != NEW.principal OR OLD.action != NEW.action
    OR OLD.reason != NEW.reason OR OLD.target_count != NEW.target_count
    OR OLD.target_digest != NEW.target_digest OR OLD.created_at != NEW.created_at
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER moderation_targets_guard BEFORE INSERT ON moderation_targets
WHEN (SELECT sealed_at FROM moderation_batches WHERE id = NEW.batch_id) IS NOT NULL
    OR EXISTS (SELECT 1 FROM moderation_targets WHERE batch_id = NEW.batch_id AND (ordinal = NEW.ordinal OR receipt_seq = NEW.receipt_seq))
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER moderation_targets_no_update BEFORE UPDATE ON moderation_targets
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER moderation_targets_no_delete BEFORE DELETE ON moderation_targets
WHEN (SELECT sealed_at FROM moderation_batches WHERE id = OLD.batch_id) IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER moderation_batches_no_replace BEFORE INSERT ON moderation_batches
WHEN EXISTS (SELECT 1 FROM moderation_batches WHERE id = NEW.id)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER moderation_batches_no_delete BEFORE DELETE ON moderation_batches
WHEN OLD.sealed_at IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER moderation_applications_no_update BEFORE UPDATE ON moderation_applications
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER moderation_applications_no_delete BEFORE DELETE ON moderation_applications
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER moderation_applications_no_replace BEFORE INSERT ON moderation_applications
WHEN EXISTS (SELECT 1 FROM moderation_applications WHERE event_seq = NEW.event_seq
    OR (batch_id = NEW.batch_id AND chunk_index = NEW.chunk_index))
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER moderate_chunk_insert INSTEAD OF INSERT ON moderate_chunk
BEGIN
    SELECT CASE WHEN EXISTS (SELECT 1 FROM pair_leases WHERE command_id = NEW.command_id)
        OR EXISTS (SELECT 1 FROM admission_results WHERE command_id = NEW.command_id)
        THEN RAISE(ABORT, 'conflict') END;
    SELECT CASE WHEN EXISTS (SELECT 1 FROM evidence_events WHERE command_id = NEW.command_id)
        THEN RAISE(ABORT, 'command_exists') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM moderation_batches b WHERE b.id = NEW.batch_id AND b.principal = NEW.principal
            AND b.sealed_at IS NOT NULL AND NEW.chunk_index >= 0 AND NEW.chunk_index * 250 < b.target_count
            AND NOT EXISTS (SELECT 1 FROM moderation_applications
                WHERE batch_id = b.id AND chunk_index = NEW.chunk_index)
    ) THEN RAISE(ABORT, 'moderation_stale') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM moderation_targets t JOIN moderation_batches b ON b.id = t.batch_id
        JOIN vote_receipts r ON r.seq = t.receipt_seq JOIN claims c ON c.id = r.claim_id
        JOIN moderation_progress p ON p.batch_id = t.batch_id AND p.claim_id = c.id
        JOIN receipt_state s ON s.receipt_seq = r.seq
        WHERE t.batch_id = NEW.batch_id AND t.ordinal / 250 = NEW.chunk_index
            AND (c.version != p.expected_version OR CASE b.action
                WHEN 'exclude' THEN s.excluded IS NOT NULL
                ELSE s.excluded IS NOT t.exclusion_seq OR t.exclusion_seq IS NULL END)
    ) THEN RAISE(ABORT, 'moderation_stale') END;

    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM pair_leases l JOIN claims c ON c.id = l.claim_id
        JOIN moderation_progress p ON p.claim_id = c.id AND p.batch_id = NEW.batch_id
        WHERE l.status = 'pending' AND c.version != p.expected_version
            AND l.repeat_of IN (SELECT receipt_seq FROM moderation_targets
                WHERE batch_id = NEW.batch_id AND ordinal / 250 = NEW.chunk_index)
    ) THEN RAISE(ABORT, 'moderation_stale') END;

    INSERT INTO evidence_events(command_id, kind, family, principal, request_hash, created_at, data)
    SELECT NEW.command_id, action, family, NEW.principal, NEW.request_hash, NEW.now_ms,
        json_object('batch_id', id, 'chunk_index', NEW.chunk_index)
    FROM moderation_batches WHERE id = NEW.batch_id;
    INSERT INTO event_targets(event_seq, ordinal, receipt_seq, claim_id, expected_version, transition, reference_seq)
    SELECT e.seq, t.ordinal % 250, t.receipt_seq, r.claim_id, c.version, b.action, t.exclusion_seq
    FROM moderation_targets t JOIN moderation_batches b ON b.id = t.batch_id
    JOIN vote_receipts r ON r.seq = t.receipt_seq JOIN claims c ON c.id = r.claim_id
    JOIN evidence_events e ON e.command_id = NEW.command_id
    WHERE t.batch_id = NEW.batch_id AND t.ordinal / 250 = NEW.chunk_index;

    INSERT INTO event_targets(event_seq, ordinal, claim_id, expected_version, transition, lease_id)
    SELECT e.seq, 250 + row_number() OVER (ORDER BY l.id) - 1, c.id, c.version, 'revoke', l.id
    FROM pair_leases l JOIN claims c ON c.id = l.claim_id
    JOIN evidence_events e ON e.command_id = NEW.command_id
    WHERE l.status = 'pending' AND (l.claim_id IN (
        SELECT claim_id FROM event_targets WHERE event_seq = e.seq)
        OR l.repeat_of IN (SELECT receipt_seq FROM event_targets WHERE event_seq = e.seq));
    UPDATE receipt_state SET excluded = CASE (SELECT action FROM moderation_batches WHERE id = NEW.batch_id)
        WHEN 'exclude' THEN (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id) ELSE NULL END
    WHERE receipt_seq IN (SELECT receipt_seq FROM event_targets
        WHERE event_seq = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id));

    UPDATE pair_leases SET status = 'revoked' WHERE id IN (SELECT lease_id FROM event_targets
        WHERE event_seq = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id) AND transition = 'revoke');
    UPDATE claims SET version = version + 1, current_lease = NULL
    WHERE id IN (SELECT claim_id FROM event_targets WHERE event_seq = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id));
    UPDATE session_state SET version = version + 1, current_lease = NULL
    WHERE current_lease IN (SELECT lease_id FROM event_targets
        WHERE event_seq = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id) AND transition = 'revoke');
    UPDATE moderation_progress SET expected_version = expected_version + 1
    WHERE batch_id = NEW.batch_id AND claim_id IN (SELECT claim_id FROM event_targets
        WHERE event_seq = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id));
    INSERT INTO refresh_coverage(claim_id) SELECT DISTINCT claim_id FROM event_targets
    WHERE event_seq = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id);
    INSERT INTO moderation_applications(batch_id, chunk_index, event_seq)
    SELECT NEW.batch_id, NEW.chunk_index, seq FROM evidence_events WHERE command_id = NEW.command_id;
    UPDATE family_state SET last_evidence_seq = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id),
        revision = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id)
    WHERE family = (SELECT family FROM moderation_batches WHERE id = NEW.batch_id);
END;

CREATE TRIGGER freeze_export_insert INSTEAD OF INSERT ON freeze_export
BEGIN
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM family_state WHERE family = NEW.family)
        THEN RAISE(ABORT, 'family_missing') END;
    INSERT INTO analysis_exports(id, family, schema_version, namespace, created_at, event_cutoff, receipt_cutoff,
        family_revision, analysis_sequence, evaluation_id, publication_id, point_id, policy_id, policy)
    SELECT NEW.id, f.family, 1, (SELECT namespace FROM deployment WHERE id = 1),
        NEW.now_ms, COALESCE((SELECT max(seq) FROM evidence_events), 0),
        COALESCE((SELECT max(seq) FROM vote_receipts), 0),
        f.revision, f.last_evidence_seq,
        f.evaluation_id, f.publication_id, f.point_id, f.policy_id, p.body
    FROM family_state f JOIN policies p ON p.id = f.policy_id WHERE f.family = NEW.family;
END;

CREATE TRIGGER analysis_exports_no_update BEFORE UPDATE ON analysis_exports
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER analysis_exports_no_delete BEFORE DELETE ON analysis_exports
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER analysis_exports_no_replace BEFORE INSERT ON analysis_exports
WHEN EXISTS (SELECT 1 FROM analysis_exports WHERE id = NEW.id)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

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

CREATE TRIGGER sessions_no_delete BEFORE DELETE ON sessions
WHEN EXISTS (SELECT 1 FROM vote_receipts WHERE session_id = OLD.id)
    OR EXISTS (SELECT 1 FROM pair_leases WHERE session_id = OLD.id)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER actor_browsing AFTER INSERT ON actors
BEGIN
    INSERT INTO actor_expiry(actor_id, expires_at) VALUES (NEW.id, NEW.created_at + 2592000000);
END;

CREATE TRIGGER session_browsing AFTER INSERT ON sessions
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

CREATE TRIGGER claims_delete_expiry BEFORE DELETE ON claims
BEGIN DELETE FROM claim_expiry WHERE claim_id = OLD.id; END;

CREATE TRIGGER sessions_delete_state BEFORE DELETE ON sessions
BEGIN
    DELETE FROM session_expiry WHERE session_id = OLD.id;
    DELETE FROM session_state WHERE session_id = OLD.id;
END;

CREATE TRIGGER actors_no_delete BEFORE DELETE ON actors
WHEN EXISTS (SELECT 1 FROM vote_receipts WHERE actor_id = OLD.id)
    OR EXISTS (SELECT 1 FROM evidence_events WHERE principal = OLD.id)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER actors_delete_state BEFORE DELETE ON actors
BEGIN
    DELETE FROM actor_expiry WHERE actor_id = OLD.id;
END;
