CREATE VIEW activate_family AS
SELECT NULL AS command_id, NULL AS request_hash, NULL AS principal, NULL AS now_ms,
    NULL AS family, NULL AS revision, NULL AS evaluation_id, NULL AS publication_id,
    NULL AS point_id WHERE 0;

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

CREATE VIEW seal_moderation AS SELECT NULL AS batch_id, NULL AS now_ms WHERE 0;

CREATE TRIGGER seal_moderation_insert INSTEAD OF INSERT ON seal_moderation
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

CREATE VIEW moderate_chunk AS
SELECT NULL AS command_id, NULL AS request_hash, NULL AS principal, NULL AS now_ms,
    NULL AS batch_id, NULL AS chunk_index WHERE 0;

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

CREATE TABLE attribution_plans (
    id TEXT PRIMARY KEY,
    family TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    actor_id TEXT NOT NULL REFERENCES actors(id),
    owner_id TEXT NOT NULL REFERENCES actors(id),
    target_count INTEGER NOT NULL CHECK (target_count >= 0),
    target_digest TEXT NOT NULL,
    sealed_at INTEGER CHECK (sealed_at >= 0)
) STRICT;

CREATE TABLE attribution_targets (
    plan_id TEXT NOT NULL REFERENCES attribution_plans(id),
    claim_id TEXT NOT NULL REFERENCES claims(id),
    version INTEGER NOT NULL CHECK (version >= 0),
    canonical_actor TEXT NOT NULL REFERENCES actors(id),
    kind TEXT NOT NULL CHECK (kind IN ('ordinary', 'repeat')),
    comparison_key TEXT NOT NULL,
    selected_receipt INTEGER REFERENCES vote_receipts(seq),
    winner_id TEXT NOT NULL REFERENCES claims(id),
    PRIMARY KEY (plan_id, claim_id)
) STRICT;

CREATE INDEX attribution_groups ON attribution_targets(plan_id, kind, comparison_key);

CREATE TRIGGER attribution_plans_no_replace BEFORE INSERT ON attribution_plans
WHEN EXISTS (SELECT 1 FROM attribution_plans WHERE id = NEW.id)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER attribution_plans_guard BEFORE UPDATE ON attribution_plans
WHEN OLD.sealed_at IS NOT NULL OR NEW.sealed_at IS NULL OR OLD.id != NEW.id
    OR OLD.family != NEW.family OR OLD.revision != NEW.revision OR OLD.actor_id != NEW.actor_id
    OR OLD.owner_id != NEW.owner_id OR OLD.target_count != NEW.target_count OR OLD.target_digest != NEW.target_digest
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER attribution_plans_no_delete BEFORE DELETE ON attribution_plans
WHEN OLD.sealed_at IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER attribution_targets_no_insert BEFORE INSERT ON attribution_targets
WHEN (SELECT sealed_at FROM attribution_plans WHERE id = NEW.plan_id) IS NOT NULL
    OR EXISTS (SELECT 1 FROM attribution_targets WHERE plan_id = NEW.plan_id AND claim_id = NEW.claim_id)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER attribution_targets_no_update BEFORE UPDATE ON attribution_targets
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER attribution_targets_no_delete BEFORE DELETE ON attribution_targets
WHEN (SELECT sealed_at FROM attribution_plans WHERE id = OLD.plan_id) IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE VIEW seal_attribution AS SELECT NULL AS plan_id, NULL AS now_ms WHERE 0;
CREATE TRIGGER seal_attribution_insert INSTEAD OF INSERT ON seal_attribution
BEGIN
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM attribution_plans p WHERE p.id = NEW.plan_id
        AND p.sealed_at IS NULL AND p.target_count = (SELECT count(*) FROM attribution_targets WHERE plan_id = p.id))
        THEN RAISE(ABORT, 'attribution_stale') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM attribution_targets t LEFT JOIN attribution_targets w
            ON w.plan_id = t.plan_id AND w.claim_id = t.winner_id
        WHERE t.plan_id = NEW.plan_id AND (w.claim_id IS NULL OR w.winner_id != w.claim_id
            OR w.kind != t.kind OR w.comparison_key != t.comparison_key)
    ) THEN RAISE(ABORT, 'attribution_stale') END;
    UPDATE attribution_plans SET sealed_at = NEW.now_ms WHERE id = NEW.plan_id;
END;

CREATE VIEW attribution_candidates AS
SELECT p.id AS plan_id, c.id AS claim_id FROM attribution_plans p
JOIN claims c ON c.family = p.family AND c.merged_into IS NULL
WHERE (p.actor_id != p.owner_id AND (
    c.canonical_actor = p.actor_id OR (c.canonical_actor = p.owner_id AND EXISTS (
        SELECT 1 FROM claims source WHERE source.family = c.family
            AND source.canonical_actor = p.actor_id AND source.merged_into IS NULL
            AND source.kind = c.kind AND source.comparison_key = c.comparison_key))))
    OR (p.actor_id = p.owner_id AND (c.selected_receipt IN (
        SELECT seq FROM vote_receipts WHERE actor_id = p.actor_id)
        OR c.current_lease IN (SELECT id FROM pair_leases WHERE actor_id = p.actor_id AND status = 'pending')));

CREATE VIEW attribute_owner AS
SELECT NULL AS command_id, NULL AS request_hash, NULL AS principal, NULL AS now_ms,
    NULL AS plan_id WHERE 0;

CREATE TRIGGER attribute_owner_insert INSTEAD OF INSERT ON attribute_owner
BEGIN
    SELECT CASE WHEN EXISTS (SELECT 1 FROM pair_leases WHERE command_id = NEW.command_id)
        OR EXISTS (SELECT 1 FROM admission_results WHERE command_id = NEW.command_id)
        THEN RAISE(ABORT, 'conflict') END;
    SELECT CASE WHEN EXISTS (SELECT 1 FROM evidence_events WHERE command_id = NEW.command_id)
        THEN RAISE(ABORT, 'command_exists') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM attribution_plans p JOIN family_state f ON f.family = p.family
        JOIN actor_resolution a ON a.family = p.family AND a.actor_id = p.actor_id
        JOIN actors o ON o.id = p.owner_id
        WHERE p.id = NEW.plan_id AND p.sealed_at IS NOT NULL AND p.revision = f.revision
            AND (p.actor_id = p.owner_id OR (o.owner_namespace IS NOT NULL
                AND NOT EXISTS (SELECT 1 FROM actor_resolution WHERE family = p.family AND actor_id = o.id AND canonical_actor != o.id)))
            AND NOT EXISTS (SELECT 1 FROM actors WHERE id = p.actor_id AND owner_namespace IS NOT NULL AND id != p.owner_id)
            AND (a.canonical_actor = a.actor_id OR p.owner_id = p.actor_id)
            AND p.target_count = (SELECT count(*) FROM attribution_candidates WHERE plan_id = p.id)
            AND NOT EXISTS (SELECT 1 FROM attribution_candidates c WHERE c.plan_id = p.id
                AND NOT EXISTS (SELECT 1 FROM attribution_targets WHERE plan_id = p.id AND claim_id = c.claim_id))
    ) THEN RAISE(ABORT, 'attribution_stale') END;

    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM attribution_targets t JOIN claims c ON c.id = t.claim_id
        WHERE t.plan_id = NEW.plan_id AND (c.version != t.version OR c.canonical_actor != t.canonical_actor
            OR c.kind != t.kind OR c.comparison_key != t.comparison_key OR c.selected_receipt IS NOT t.selected_receipt
            OR c.merged_into IS NOT NULL)
    ) THEN RAISE(ABORT, 'attribution_stale') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM attribution_targets t JOIN attribution_targets w ON w.plan_id = t.plan_id AND w.claim_id = t.winner_id
        LEFT JOIN vote_receipts r ON r.seq = t.selected_receipt
        LEFT JOIN vote_receipts winner ON winner.seq = w.selected_receipt
        WHERE t.plan_id = NEW.plan_id AND (
            COALESCE(r.recorded_at, -1) > COALESCE(winner.recorded_at, -1)
            OR (COALESCE(r.recorded_at, -1) = COALESCE(winner.recorded_at, -1) AND COALESCE(r.seq, 0) > COALESCE(winner.seq, 0))
            OR (r.seq IS winner.seq AND t.claim_id < w.claim_id))
    ) THEN RAISE(ABORT, 'attribution_stale') END;

    INSERT INTO evidence_events(command_id, kind, family, principal, request_hash, created_at, data)
    SELECT NEW.command_id, 'attribute', family, NEW.principal, NEW.request_hash, NEW.now_ms,
        json_object('plan_id', id, 'actor_id', actor_id, 'canonical_actor', owner_id)
    FROM attribution_plans WHERE id = NEW.plan_id;
    INSERT INTO event_targets(event_seq, ordinal, receipt_seq, claim_id, expected_version,
        transition, lease_id, target_claim, target_actor)
    SELECT e.seq, row_number() OVER (ORDER BY t.claim_id) - 1, t.selected_receipt,
        t.claim_id, t.version, CASE WHEN t.claim_id = t.winner_id THEN 'attribute' ELSE 'supersede' END,
        c.current_lease, t.winner_id, p.owner_id
    FROM attribution_targets t JOIN attribution_plans p ON p.id = t.plan_id
    JOIN claims c ON c.id = t.claim_id JOIN evidence_events e ON e.command_id = NEW.command_id
    WHERE t.plan_id = NEW.plan_id;

    UPDATE pair_leases SET status = 'revoked' WHERE id IN (
        SELECT lease_id FROM event_targets WHERE event_seq = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id));
    UPDATE session_state SET version = version + 1, current_lease = NULL WHERE current_lease IN (
        SELECT lease_id FROM event_targets WHERE event_seq = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id));
    UPDATE receipt_state SET superseded = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id)
    WHERE receipt_seq IN (SELECT receipt_seq FROM event_targets
        WHERE event_seq = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id) AND transition = 'supersede');
    UPDATE claims SET merged_into = (SELECT winner_id FROM attribution_targets WHERE plan_id = NEW.plan_id AND claim_id = claims.id)
    WHERE id IN (SELECT claim_id FROM attribution_targets WHERE plan_id = NEW.plan_id AND claim_id != winner_id);
    UPDATE claims SET canonical_actor = (SELECT owner_id FROM attribution_plans WHERE id = NEW.plan_id),
        current_lease = NULL, version = version + 1
    WHERE id IN (SELECT claim_id FROM attribution_targets WHERE plan_id = NEW.plan_id);
    UPDATE actor_resolution SET canonical_actor = (SELECT owner_id FROM attribution_plans WHERE id = NEW.plan_id),
        event_seq = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id)
    WHERE family = (SELECT family FROM attribution_plans WHERE id = NEW.plan_id)
        AND actor_id = (SELECT actor_id FROM attribution_plans WHERE id = NEW.plan_id);
    INSERT INTO refresh_coverage(claim_id) SELECT claim_id FROM attribution_targets WHERE plan_id = NEW.plan_id;
    UPDATE family_state SET last_evidence_seq = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id),
        revision = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id)
    WHERE family = (SELECT family FROM attribution_plans WHERE id = NEW.plan_id);
END;

CREATE TABLE analysis_exports (
    id TEXT PRIMARY KEY,
    family TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    namespace TEXT NOT NULL,
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    event_cutoff INTEGER NOT NULL CHECK (event_cutoff >= 0),
    receipt_cutoff INTEGER NOT NULL CHECK (receipt_cutoff >= 0),
    import_cutoff INTEGER NOT NULL CHECK (import_cutoff >= 0),
    import_source_cutoff INTEGER NOT NULL CHECK (import_source_cutoff >= 0),
    family_revision INTEGER NOT NULL CHECK (family_revision >= 0),
    analysis_sequence INTEGER NOT NULL CHECK (analysis_sequence >= 0),
    evaluation_id TEXT NOT NULL REFERENCES evaluations(id),
    publication_id TEXT NOT NULL REFERENCES publications(id),
    point_id TEXT NOT NULL REFERENCES point_snapshots(id),
    policy_id TEXT NOT NULL REFERENCES policies(id),
    policy TEXT NOT NULL CHECK (json_valid(policy))
) STRICT;

CREATE VIEW freeze_export AS SELECT NULL AS id, NULL AS family, NULL AS now_ms WHERE 0;
CREATE TRIGGER freeze_export_insert INSTEAD OF INSERT ON freeze_export
BEGIN
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM family_state WHERE family = NEW.family)
        THEN RAISE(ABORT, 'family_missing') END;
    INSERT INTO analysis_exports(id, family, schema_version, namespace, created_at, event_cutoff, receipt_cutoff, import_cutoff, import_source_cutoff,
        family_revision, analysis_sequence, evaluation_id, publication_id, point_id, policy_id, policy)
    SELECT NEW.id, f.family, 1, (SELECT namespace FROM deployment WHERE id = 1),
        NEW.now_ms, COALESCE((SELECT max(seq) FROM evidence_events), 0),
        COALESCE((SELECT max(seq) FROM vote_receipts), 0),
        COALESCE((SELECT max(seq) FROM import_rows), 0),
        COALESCE((SELECT max(seq) FROM import_sources), 0), f.revision, f.last_evidence_seq,
        f.evaluation_id, f.publication_id, f.point_id, f.policy_id, p.body
    FROM family_state f JOIN policies p ON p.id = f.policy_id WHERE f.family = NEW.family;
END;

CREATE TABLE import_sources (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    family TEXT NOT NULL,
    owner_id TEXT NOT NULL REFERENCES actors(id),
    source_schema TEXT NOT NULL,
    file_digest TEXT NOT NULL,
    session_count INTEGER NOT NULL CHECK (session_count >= 0),
    receipt_count INTEGER NOT NULL CHECK (receipt_count >= 0),
    withdrawal_count INTEGER NOT NULL CHECK (withdrawal_count >= 0),
    completed_at INTEGER CHECK (completed_at >= 0)
) STRICT;

CREATE TABLE import_rows (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES import_sources(id),
    row_kind TEXT NOT NULL CHECK (row_kind IN ('session', 'receipt', 'withdrawal')),
    original_id TEXT NOT NULL,
    row_hash TEXT NOT NULL,
    raw TEXT NOT NULL CHECK (json_valid(raw)),
    initial_disposition TEXT NOT NULL CHECK (initial_disposition IN ('unresolved', 'incompatible')),
    UNIQUE (source_id, row_kind, original_id)
) STRICT;

CREATE TRIGGER import_rows_local BEFORE INSERT ON import_rows
WHEN (SELECT mode FROM deployment WHERE id = 1) != 'local'
BEGIN SELECT RAISE(ABORT, 'private_material'); END;

CREATE TRIGGER import_rows_shape BEFORE INSERT ON import_rows
WHEN EXISTS (SELECT 1 FROM json_tree(NEW.raw) WHERE key IN ('note', 'notes', 'annotation', 'annotations'))
    OR (NEW.row_kind = 'session' AND json_extract(NEW.raw, '$.family') != (SELECT family FROM import_sources WHERE id = NEW.source_id))
    OR (NEW.row_kind = 'receipt' AND NOT EXISTS (SELECT 1 FROM import_rows s
        WHERE s.source_id = NEW.source_id AND s.row_kind = 'session'
            AND s.original_id = json_extract(NEW.raw, '$.session')))
    OR (NEW.row_kind = 'withdrawal' AND NOT EXISTS (SELECT 1 FROM import_rows r
        WHERE r.source_id = NEW.source_id AND r.row_kind = 'receipt'
            AND CAST(r.original_id AS INTEGER) = json_extract(NEW.raw, '$.vote')))
BEGIN SELECT RAISE(ABORT, 'import_invalid'); END;

CREATE TRIGGER analysis_exports_no_update BEFORE UPDATE ON analysis_exports
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER analysis_exports_no_delete BEFORE DELETE ON analysis_exports
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER analysis_exports_no_replace BEFORE INSERT ON analysis_exports
WHEN EXISTS (SELECT 1 FROM analysis_exports WHERE id = NEW.id)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER import_rows_no_update BEFORE UPDATE ON import_rows
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER import_rows_no_delete BEFORE DELETE ON import_rows
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER import_rows_no_replace BEFORE INSERT ON import_rows
WHEN EXISTS (SELECT 1 FROM import_rows WHERE seq = NEW.seq OR (source_id = NEW.source_id AND row_kind = NEW.row_kind AND original_id = NEW.original_id))
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER import_sources_no_replace BEFORE INSERT ON import_sources
WHEN EXISTS (SELECT 1 FROM import_sources WHERE id = NEW.id OR seq = NEW.seq)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER import_sources_guard BEFORE UPDATE ON import_sources
WHEN OLD.id != NEW.id OR OLD.owner_id != NEW.owner_id OR OLD.source_schema != NEW.source_schema
    OR OLD.seq != NEW.seq OR OLD.family != NEW.family OR OLD.file_digest != NEW.file_digest
    OR OLD.session_count != NEW.session_count OR OLD.receipt_count != NEW.receipt_count
    OR OLD.withdrawal_count != NEW.withdrawal_count OR OLD.completed_at IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER import_sources_no_delete BEFORE DELETE ON import_sources
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

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

CREATE VIEW observation_bodies(seq, body) AS
SELECT r.seq, json_patch(json_object(
    'seq', r.seq, 'acknowledgment', r.acknowledgment, 'origin', r.origin,
    'actor_id', r.actor_id, 'session_id', r.session_id, 'family', r.family,
    'claim_id', r.claim_id,
    'case_key', r.case_key, 'a_output', r.a_output, 'b_output', r.b_output,
    'a_system', r.a_system, 'b_system', r.b_system, 'a_fingerprint', r.a_fingerprint,
    'b_fingerprint', r.b_fingerprint, 'choice', r.choice, 'defects', json(r.defects),
    'repeat_of', r.repeat_of, 'recorded_at', r.recorded_at),
    CASE r.origin WHEN 'served' THEN json_object(
        'lease_id', r.lease_id, 'evaluation_id', r.evaluation_id,
        'publication_id', r.publication_id, 'point_id', r.point_id,
        'claim_version', r.claim_version, 'duration_ms', r.duration_ms)
    ELSE json_object('import_source', r.import_source, 'original_id', r.original_id,
    'import_row_seq', r.import_row_seq, 'proof_evaluation_id', r.proof_evaluation_id,
    'legacy_duration_sec', r.legacy_duration_sec) END)
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
    'transition', t.transition, 'reference_seq', t.reference_seq, 'lease_id', t.lease_id,
    'target_claim', t.target_claim, 'target_actor', t.target_actor, 'source_row', t.source_row)
FROM analysis_exports x JOIN evidence_events e ON e.family = x.family AND e.seq <= x.event_cutoff
JOIN event_targets t ON t.event_seq = e.seq;

CREATE VIEW export_actors(export_id, section, row_key, body) AS
SELECT x.id, 'actors', a.id, json_object('id', a.id, 'created_at', a.created_at, 'owner_namespace', a.owner_namespace)
FROM analysis_exports x JOIN actors a ON a.id IN (
    SELECT actor_id FROM vote_receipts WHERE family = x.family AND seq <= x.receipt_cutoff
    UNION SELECT json_extract(data, '$.actor_id') FROM evidence_events
        WHERE family = x.family AND seq <= x.event_cutoff AND kind = 'attribute'
    UNION SELECT json_extract(data, '$.canonical_actor') FROM evidence_events
        WHERE family = x.family AND seq <= x.event_cutoff AND kind = 'attribute'
    UNION SELECT owner_id FROM import_sources WHERE family = x.family AND seq <= x.import_source_cutoff);

CREATE VIEW export_sessions(export_id, section, row_key, body) AS
SELECT x.id, 'sessions', s.id, json_object('id', s.id, 'actor_id', s.actor_id,
    'family', s.family, 'created_at', s.created_at, 'import_source', s.import_source,
    'original_id', s.original_id)
FROM analysis_exports x JOIN sessions s ON s.id IN (
    SELECT session_id FROM vote_receipts WHERE family = x.family AND seq <= x.receipt_cutoff);

CREATE VIEW export_import_sources(export_id, section, row_key, body) AS
SELECT x.id, 'import_sources', i.id, json_object('id', i.id, 'family', i.family,
    'owner_id', i.owner_id, 'source_schema', i.source_schema, 'file_digest', i.file_digest,
    'sessions', i.session_count, 'receipts', i.receipt_count, 'withdrawals', i.withdrawal_count)
FROM analysis_exports x JOIN import_sources i ON i.family = x.family AND i.seq <= x.import_source_cutoff;

CREATE VIEW export_import_rows(export_id, section, row_key, body) AS
SELECT x.id, 'import_rows', json_array(i.source_id, i.row_kind, i.original_id), json_object(
    'source_id', i.source_id, 'row_kind', i.row_kind, 'original_id', i.original_id,
    'seq', i.seq, 'row_hash', i.row_hash, 'initial_disposition', i.initial_disposition,
    'repeat_of', json_extract(i.raw, '$.repeat_of'))
FROM analysis_exports x JOIN import_sources s ON s.family = x.family AND s.seq <= x.import_source_cutoff
JOIN import_rows i ON i.source_id = s.id AND i.seq <= x.import_cutoff;
