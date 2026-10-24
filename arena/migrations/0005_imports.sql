CREATE TABLE import_plans (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES import_sources(id),
    body TEXT NOT NULL CHECK (json_valid(body) AND length(CAST(body AS BLOB)) <= 524288),
    body_hash TEXT NOT NULL,
    sealed_at INTEGER CHECK (sealed_at >= 0)
) STRICT;

CREATE TRIGGER import_sources_local BEFORE INSERT ON import_sources
WHEN (SELECT mode FROM deployment WHERE id = 1) != 'local'
    OR NOT EXISTS (SELECT 1 FROM actors WHERE id = NEW.owner_id AND owner_namespace IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'private_material'); END;

CREATE TABLE import_applications (
    plan_id TEXT PRIMARY KEY REFERENCES import_plans(id),
    event_seq INTEGER NOT NULL UNIQUE REFERENCES evidence_events(seq)
) STRICT;

CREATE TABLE import_execution (
    plan_id TEXT PRIMARY KEY REFERENCES import_plans(id),
    command_id TEXT NOT NULL UNIQUE,
    principal TEXT NOT NULL,
    now_ms INTEGER NOT NULL CHECK (now_ms >= 0)
) STRICT;

CREATE TRIGGER import_plans_no_replace BEFORE INSERT ON import_plans
WHEN EXISTS (SELECT 1 FROM import_plans WHERE id = NEW.id)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER import_plans_guard BEFORE UPDATE ON import_plans
WHEN OLD.sealed_at IS NOT NULL OR NEW.sealed_at IS NULL OR OLD.id != NEW.id
    OR OLD.source_id != NEW.source_id OR OLD.body != NEW.body OR OLD.body_hash != NEW.body_hash
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER import_plans_no_delete BEFORE DELETE ON import_plans
WHEN OLD.sealed_at IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER import_applications_no_update BEFORE UPDATE ON import_applications
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER import_applications_no_delete BEFORE DELETE ON import_applications
BEGIN SELECT RAISE(ABORT, 'immutable'); END;
CREATE TRIGGER import_applications_no_replace BEFORE INSERT ON import_applications
WHEN EXISTS (SELECT 1 FROM import_applications WHERE plan_id = NEW.plan_id OR event_seq = NEW.event_seq)
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE TRIGGER import_rows_complete BEFORE INSERT ON import_rows
WHEN (SELECT completed_at FROM import_sources WHERE id = NEW.source_id) IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'immutable'); END;

CREATE VIEW import_bindings AS
SELECT p.id AS plan_id, p.source_id, json_extract(b.value, '$.raw_seq') AS raw_seq,
    json_extract(b.value, '$.raw_hash') AS raw_hash,
    json_extract(b.value, '$.command_id') AS command_id,
    json_extract(b.value, '$.acknowledgment') AS acknowledgment,
    json_extract(b.value, '$.claim_id') AS claim_id,
    json_extract(b.value, '$.proof_evaluation_id') AS proof_evaluation_id,
    json_extract(b.value, '$.case_key') AS case_key,
    json_extract(b.value, '$.a_output') AS a_output,
    json_extract(b.value, '$.b_output') AS b_output,
    json_extract(b.value, '$.a_source_digest') AS a_source_digest,
    json_extract(b.value, '$.b_source_digest') AS b_source_digest,
    json_extract(b.value, '$.recorded_at') AS recorded_at
FROM import_plans p, json_each(p.body, '$.observations') b;

CREATE VIEW import_selections AS
SELECT p.id AS plan_id, json_extract(s.value, '$.claim_id') AS claim_id,
    json_extract(s.value, '$.expected_version') AS expected_version,
    json_extract(s.value, '$.expected_receipt') AS expected_receipt,
    json_extract(s.value, '$.selected_acknowledgment') AS selected_acknowledgment
FROM import_plans p, json_each(p.body, '$.selections') s;

CREATE VIEW import_session_bindings AS
SELECT p.id AS plan_id, p.source_id, json_extract(s.value, '$.original_id') AS original_id,
    json_extract(s.value, '$.session_id') AS session_id,
    json_extract(s.value, '$.created_at') AS created_at
FROM import_plans p, json_each(p.body, '$.sessions') s;

CREATE VIEW import_dispositions AS
SELECT p.id AS plan_id, p.source_id, json_extract(s.value, '$.raw_seq') AS raw_seq,
    json_extract(s.value, '$.state') AS state, json_extract(s.value, '$.reason') AS reason
FROM import_plans p, json_each(p.body, '$.dispositions') s;

CREATE VIEW seal_import AS SELECT NULL AS plan_id, NULL AS now_ms WHERE 0;
CREATE TRIGGER seal_import_insert INSTEAD OF INSERT ON seal_import
BEGIN
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM import_plans p
        JOIN import_sources s ON s.id = p.source_id
        WHERE p.id = NEW.plan_id AND p.sealed_at IS NULL
            AND (SELECT mode FROM deployment WHERE id = 1) = 'local'
            AND json_array_length(p.body, '$.observations') <= 250
            AND json_array_length(p.body, '$.selections') <= 250
            AND CASE WHEN json_extract(p.body, '$.initial') THEN s.completed_at IS NULL
                AND s.session_count = (SELECT count(*) FROM import_rows WHERE source_id = s.id AND row_kind = 'session')
                AND s.receipt_count = (SELECT count(*) FROM import_rows WHERE source_id = s.id AND row_kind = 'receipt')
                AND s.withdrawal_count = (SELECT count(*) FROM import_rows WHERE source_id = s.id AND row_kind = 'withdrawal')
                AND s.receipt_count = (SELECT count(*) FROM import_dispositions WHERE plan_id = p.id)
                AND s.session_count = (SELECT count(*) FROM import_session_bindings WHERE plan_id = p.id)
                ELSE s.completed_at IS NOT NULL END)
        THEN RAISE(ABORT, 'import_invalid') END;

    SELECT CASE WHEN EXISTS (SELECT 1 FROM import_dispositions d
        LEFT JOIN import_rows r ON r.seq = d.raw_seq AND r.source_id = d.source_id AND r.row_kind = 'receipt'
        WHERE d.plan_id = NEW.plan_id AND (r.seq IS NULL
            OR (d.state = 'linked') != EXISTS (SELECT 1 FROM import_bindings b WHERE b.plan_id = d.plan_id AND b.raw_seq = d.raw_seq)))
        OR (SELECT count(*) FROM import_dispositions WHERE plan_id = NEW.plan_id)
            != (SELECT count(DISTINCT raw_seq) FROM import_dispositions WHERE plan_id = NEW.plan_id)
        THEN RAISE(ABORT, 'import_invalid') END;

    SELECT CASE WHEN EXISTS (SELECT 1 FROM import_session_bindings b
        LEFT JOIN import_rows r ON r.source_id = b.source_id AND r.row_kind = 'session' AND r.original_id = b.original_id
        WHERE b.plan_id = NEW.plan_id AND r.seq IS NULL)
        THEN RAISE(ABORT, 'import_invalid') END;

    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM import_bindings b JOIN import_sources s ON s.id = b.source_id
        LEFT JOIN import_rows r ON r.seq = b.raw_seq AND r.source_id = s.id AND r.row_kind = 'receipt'
        LEFT JOIN evaluations e ON e.id = b.proof_evaluation_id AND e.family = s.family AND e.sealed_at IS NOT NULL
        LEFT JOIN evaluation_outputs a ON a.evaluation_id = e.id AND a.output_key = b.a_output
        LEFT JOIN evaluation_outputs z ON z.evaluation_id = e.id AND z.output_key = b.b_output
        LEFT JOIN evaluation_systems sa ON sa.evaluation_id = e.id AND sa.system_id = a.system_id
        LEFT JOIN evaluation_systems sz ON sz.evaluation_id = e.id AND sz.system_id = z.system_id
        WHERE b.plan_id = NEW.plan_id AND (r.seq IS NULL OR r.row_hash != b.raw_hash OR e.id IS NULL
            OR a.output_key IS NULL OR z.output_key IS NULL OR a.system_id = z.system_id
            OR a.case_key != b.case_key OR z.case_key != b.case_key
            OR a.system_id IS NOT json_extract(r.raw, '$.a_system') OR z.system_id IS NOT json_extract(r.raw, '$.b_system')
            OR sa.fingerprint IS NOT json_extract(r.raw, '$.a_fingerprint') OR sz.fingerprint IS NOT json_extract(r.raw, '$.b_fingerprint')
            OR json_extract(a.body, '$.metadata.source_digest') IS NOT b.a_source_digest
            OR json_extract(z.body, '$.metadata.source_digest') IS NOT b.b_source_digest
            OR NOT EXISTS (SELECT 1 FROM import_dispositions d WHERE d.plan_id = b.plan_id AND d.raw_seq = b.raw_seq AND d.state = 'linked')
            OR NOT EXISTS (SELECT 1 FROM import_selections t WHERE t.plan_id = b.plan_id AND t.claim_id = b.claim_id)
            OR NOT EXISTS (SELECT 1 FROM import_session_bindings t WHERE t.plan_id = b.plan_id AND t.original_id = json_extract(r.raw, '$.session')))
    ) THEN RAISE(ABORT, 'import_invalid') END;
    UPDATE import_plans SET sealed_at = NEW.now_ms WHERE id = NEW.plan_id;
END;

CREATE VIEW import_observation AS SELECT NULL AS plan_id, NULL AS raw_seq WHERE 0;
CREATE TRIGGER import_observation_insert INSTEAD OF INSERT ON import_observation
BEGIN
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM import_execution WHERE plan_id = NEW.plan_id)
        THEN RAISE(ABORT, 'import_invalid') END;
    SELECT CASE WHEN EXISTS (SELECT 1 FROM import_bindings b
        WHERE b.plan_id = NEW.plan_id AND b.raw_seq = NEW.raw_seq AND (
            EXISTS (SELECT 1 FROM pair_leases WHERE command_id = b.command_id)
            OR EXISTS (SELECT 1 FROM admission_results WHERE command_id = b.command_id)))
        THEN RAISE(ABORT, 'conflict') END;
    SELECT CASE WHEN EXISTS (SELECT 1 FROM import_bindings b JOIN import_rows raw ON raw.seq = b.raw_seq
        WHERE b.plan_id = NEW.plan_id AND b.raw_seq = NEW.raw_seq
            AND json_extract(raw.raw, '$.repeat_of') IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM vote_receipts r WHERE r.import_source = b.source_id
                AND CAST(r.original_id AS INTEGER) = json_extract(raw.raw, '$.repeat_of')
                AND r.repeat_of IS NULL AND b.a_output = r.b_output AND b.b_output = r.a_output
                AND b.recorded_at >= r.recorded_at + 86400000))
        THEN RAISE(ABORT, 'import_invalid') END;

    INSERT INTO claims(id, family, canonical_actor, kind, comparison_key, repeat_of)
    SELECT b.claim_id, s.family, s.owner_id,
        CASE WHEN json_extract(raw.raw, '$.repeat_of') IS NULL THEN 'ordinary' ELSE 'repeat' END,
        CASE WHEN json_extract(raw.raw, '$.repeat_of') IS NULL THEN json_array(b.case_key,
            min(json_extract(raw.raw, '$.a_system'), json_extract(raw.raw, '$.b_system')),
            max(json_extract(raw.raw, '$.a_system'), json_extract(raw.raw, '$.b_system')))
            ELSE CAST(original.seq AS TEXT) END, original.seq
    FROM import_bindings b JOIN import_sources s ON s.id = b.source_id JOIN import_rows raw ON raw.seq = b.raw_seq
    LEFT JOIN vote_receipts original ON original.import_source = s.id
        AND CAST(original.original_id AS INTEGER) = json_extract(raw.raw, '$.repeat_of')
    WHERE b.plan_id = NEW.plan_id AND b.raw_seq = NEW.raw_seq AND NOT EXISTS (SELECT 1 FROM claims WHERE id = b.claim_id);

    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM import_bindings b JOIN claims c ON c.id = b.claim_id
        JOIN import_sources s ON s.id = b.source_id JOIN import_rows raw ON raw.seq = b.raw_seq
        JOIN import_selections t ON t.plan_id = b.plan_id AND t.claim_id = c.id
        LEFT JOIN vote_receipts original ON original.import_source = s.id
            AND CAST(original.original_id AS INTEGER) = json_extract(raw.raw, '$.repeat_of')
        WHERE b.plan_id = NEW.plan_id AND b.raw_seq = NEW.raw_seq AND c.family = s.family
            AND c.canonical_actor = s.owner_id AND c.merged_into IS NULL AND c.version = t.expected_version
            AND c.selected_receipt IS t.expected_receipt
            AND c.repeat_of IS original.seq AND c.comparison_key = CASE WHEN original.seq IS NULL THEN
                json_array(b.case_key, min(json_extract(raw.raw, '$.a_system'), json_extract(raw.raw, '$.b_system')),
                    max(json_extract(raw.raw, '$.a_system'), json_extract(raw.raw, '$.b_system')))
                ELSE CAST(original.seq AS TEXT) END)
        THEN RAISE(ABORT, 'import_invalid') END;

    INSERT INTO evidence_events(command_id, kind, family, principal, request_hash, created_at, data)
    SELECT b.command_id, 'import', s.family, x.principal, p.body_hash, x.now_ms,
        json_object('kind', 'observation', 'source_id', s.id, 'raw_seq', b.raw_seq,
            'proof_evaluation_id', b.proof_evaluation_id, 'plan_id', p.id)
    FROM import_bindings b JOIN import_sources s ON s.id = b.source_id
    JOIN import_plans p ON p.id = b.plan_id JOIN import_execution x ON x.plan_id = p.id
    WHERE b.plan_id = NEW.plan_id AND b.raw_seq = NEW.raw_seq;

    INSERT INTO vote_receipts(seq, acknowledgment, origin, actor_id, session_id, family,
        claim_id, case_key, a_output, b_output, a_system, b_system, a_fingerprint, b_fingerprint,
        choice, defects, repeat_of, recorded_at, import_source, original_id,
        import_row_seq, proof_evaluation_id, legacy_duration_sec)
    SELECT e.seq, b.acknowledgment, 'legacy', s.owner_id, session.id, s.family,
        b.claim_id, b.case_key, b.a_output, b.b_output, a.system_id, z.system_id,
        json_extract(raw.raw, '$.a_fingerprint'), json_extract(raw.raw, '$.b_fingerprint'),
        json_extract(raw.raw, '$.choice'), json_object('a', json(json_extract(raw.raw, '$.a_defects')),
            'b', json(json_extract(raw.raw, '$.b_defects'))), original.seq,
        b.recorded_at, s.id, raw.original_id, raw.seq, b.proof_evaluation_id, json_extract(raw.raw, '$.duration_sec')
    FROM import_bindings b JOIN import_sources s ON s.id = b.source_id JOIN import_rows raw ON raw.seq = b.raw_seq
    JOIN sessions session ON session.import_source = s.id AND session.original_id = json_extract(raw.raw, '$.session')
    JOIN evaluation_outputs a ON a.evaluation_id = b.proof_evaluation_id AND a.output_key = b.a_output
    JOIN evaluation_outputs z ON z.evaluation_id = b.proof_evaluation_id AND z.output_key = b.b_output
    JOIN evidence_events e ON e.command_id = b.command_id
    LEFT JOIN vote_receipts original ON original.import_source = s.id
        AND CAST(original.original_id AS INTEGER) = json_extract(raw.raw, '$.repeat_of')
    WHERE b.plan_id = NEW.plan_id AND b.raw_seq = NEW.raw_seq;
    INSERT INTO receipt_state(receipt_seq) SELECT e.seq FROM evidence_events e
    JOIN import_bindings b ON b.command_id = e.command_id WHERE b.plan_id = NEW.plan_id AND b.raw_seq = NEW.raw_seq;
END;

CREATE VIEW apply_import AS
SELECT NULL AS command_id, NULL AS request_hash, NULL AS principal, NULL AS now_ms, NULL AS plan_id WHERE 0;
CREATE TRIGGER apply_import_insert INSTEAD OF INSERT ON apply_import
BEGIN
    SELECT CASE WHEN EXISTS (SELECT 1 FROM pair_leases WHERE command_id = NEW.command_id)
        OR EXISTS (SELECT 1 FROM admission_results WHERE command_id = NEW.command_id)
        THEN RAISE(ABORT, 'conflict') END;
    SELECT CASE WHEN EXISTS (SELECT 1 FROM evidence_events WHERE command_id = NEW.command_id)
        THEN RAISE(ABORT, 'command_exists') END;
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM import_plans p JOIN import_sources s ON s.id = p.source_id
        JOIN family_state f ON f.family = s.family WHERE p.id = NEW.plan_id AND p.sealed_at IS NOT NULL
            AND f.revision = json_extract(p.body, '$.revision')
            AND NOT EXISTS (SELECT 1 FROM import_applications WHERE plan_id = p.id)
            AND (json_extract(p.body, '$.initial') = (s.completed_at IS NULL))
            AND (SELECT mode FROM deployment WHERE id = 1) = 'local')
        THEN RAISE(ABORT, 'import_invalid') END;
    SELECT CASE WHEN EXISTS (SELECT 1 FROM import_selections t
        JOIN import_plans p ON p.id = t.plan_id JOIN import_sources s ON s.id = p.source_id
        LEFT JOIN claims c ON c.id = t.claim_id WHERE t.plan_id = NEW.plan_id AND (
            (c.id IS NULL AND (t.expected_version != 0 OR t.expected_receipt IS NOT NULL))
            OR (c.id IS NOT NULL AND (c.version != t.expected_version OR c.selected_receipt IS NOT t.expected_receipt
                OR c.family != s.family OR c.canonical_actor != s.owner_id OR c.merged_into IS NOT NULL))))
        THEN RAISE(ABORT, 'import_invalid') END;

    SELECT CASE WHEN EXISTS (SELECT 1 FROM import_session_bindings b JOIN sessions s ON s.id = b.session_id
        JOIN import_sources source ON source.id = b.source_id WHERE b.plan_id = NEW.plan_id
            AND (s.import_source IS NOT b.source_id OR s.original_id IS NOT b.original_id
                OR s.actor_id != source.owner_id OR s.family != source.family OR s.created_at != b.created_at))
        THEN RAISE(ABORT, 'import_invalid') END;

    INSERT INTO sessions(id, actor_id, family, created_at, import_source, original_id)
    SELECT b.session_id, s.owner_id, s.family, b.created_at, s.id, b.original_id
    FROM import_session_bindings b JOIN import_sources s ON s.id = b.source_id
    WHERE b.plan_id = NEW.plan_id AND NOT EXISTS (SELECT 1 FROM sessions WHERE id = b.session_id);
    INSERT INTO import_execution(plan_id, command_id, principal, now_ms)
    VALUES (NEW.plan_id, NEW.command_id, NEW.principal, NEW.now_ms);
    INSERT INTO import_observation(plan_id, raw_seq)
    SELECT b.plan_id, b.raw_seq FROM import_bindings b JOIN import_rows r ON r.seq = b.raw_seq
    WHERE b.plan_id = NEW.plan_id ORDER BY CAST(r.original_id AS INTEGER);

    INSERT INTO evidence_events(command_id, kind, family, principal, request_hash, created_at, data)
    SELECT NEW.command_id, 'import', s.family, NEW.principal, NEW.request_hash, NEW.now_ms,
        json_object('kind', CASE WHEN json_extract(p.body, '$.initial') THEN 'finalize' ELSE 'resolve' END,
            'source_id', s.id, 'plan_id', p.id, 'dispositions', json_extract(p.body, '$.dispositions'))
    FROM import_plans p JOIN import_sources s ON s.id = p.source_id WHERE p.id = NEW.plan_id;

    INSERT INTO event_targets(event_seq, ordinal, receipt_seq, claim_id, expected_version, transition, source_row)
    SELECT e.seq, row_number() OVER (ORDER BY r.seq) - 1, r.seq, r.claim_id, c.version, 'withdraw', undo.seq
    FROM import_plans p JOIN vote_receipts r ON r.import_source = p.source_id
    JOIN receipt_state state ON state.receipt_seq = r.seq AND state.withdrawn IS NULL
    JOIN claims c ON c.id = r.claim_id JOIN import_rows undo ON undo.source_id = p.source_id
        AND undo.row_kind = 'withdrawal' AND undo.original_id = r.original_id
    JOIN evidence_events e ON e.command_id = NEW.command_id WHERE p.id = NEW.plan_id;
    UPDATE receipt_state SET withdrawn = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id)
    WHERE receipt_seq IN (SELECT receipt_seq FROM event_targets
        WHERE event_seq = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id) AND transition = 'withdraw');

    SELECT CASE WHEN EXISTS (SELECT 1 FROM import_selections t
        JOIN vote_receipts chosen ON chosen.acknowledgment = t.selected_acknowledgment
        JOIN import_plans p ON p.id = t.plan_id
        WHERE t.plan_id = NEW.plan_id AND chosen.import_source = p.source_id AND chosen.repeat_of IS NULL
            AND EXISTS (SELECT 1 FROM vote_receipts newer JOIN receipt_state state ON state.receipt_seq = newer.seq
                WHERE newer.import_source = p.source_id AND newer.claim_id = chosen.claim_id
                    AND CAST(newer.original_id AS INTEGER) > CAST(chosen.original_id AS INTEGER)
                    AND state.withdrawn IS NULL))
        THEN RAISE(ABORT, 'import_invalid') END;

    SELECT CASE WHEN EXISTS (SELECT 1 FROM import_selections t
        LEFT JOIN vote_receipts r ON r.acknowledgment = t.selected_acknowledgment
        LEFT JOIN receipt_state state ON state.receipt_seq = r.seq
        WHERE t.plan_id = NEW.plan_id AND t.selected_acknowledgment IS NOT NULL
            AND (r.seq IS NULL OR r.claim_id != t.claim_id OR state.withdrawn IS NOT NULL OR state.superseded IS NOT NULL))
        THEN RAISE(ABORT, 'import_invalid') END;

    INSERT INTO event_targets(event_seq, ordinal, receipt_seq, claim_id, expected_version, transition)
    SELECT e.seq, 250 + row_number() OVER (ORDER BY c.id) - 1, c.selected_receipt, c.id, c.version, 'supersede'
    FROM import_selections t JOIN claims c ON c.id = t.claim_id
    JOIN evidence_events e ON e.command_id = NEW.command_id
    WHERE t.plan_id = NEW.plan_id AND c.selected_receipt IS NOT NULL AND c.selected_receipt IS NOT (
        SELECT seq FROM vote_receipts WHERE acknowledgment = t.selected_acknowledgment);
    INSERT INTO event_targets(event_seq, ordinal, receipt_seq, claim_id, expected_version, transition)
    SELECT e.seq, 500 + row_number() OVER (ORDER BY r.seq) - 1, r.seq, r.claim_id, c.version, 'supersede'
    FROM import_bindings b JOIN vote_receipts r ON r.import_row_seq = b.raw_seq JOIN claims c ON c.id = r.claim_id
    JOIN import_selections t ON t.plan_id = b.plan_id AND t.claim_id = r.claim_id
    JOIN evidence_events e ON e.command_id = NEW.command_id
    WHERE b.plan_id = NEW.plan_id AND r.acknowledgment IS NOT t.selected_acknowledgment;
    UPDATE receipt_state SET superseded = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id)
    WHERE receipt_seq IN (SELECT receipt_seq FROM event_targets
        WHERE event_seq = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id) AND transition = 'supersede');

    INSERT INTO event_targets(event_seq, ordinal, receipt_seq, claim_id, expected_version, transition)
    SELECT e.seq, 750 + row_number() OVER (ORDER BY t.claim_id) - 1, r.seq, t.claim_id, c.version, 'select'
    FROM import_selections t JOIN claims c ON c.id = t.claim_id
    LEFT JOIN vote_receipts r ON r.acknowledgment = t.selected_acknowledgment
    JOIN evidence_events e ON e.command_id = NEW.command_id WHERE t.plan_id = NEW.plan_id;
    INSERT INTO event_targets(event_seq, ordinal, claim_id, expected_version, transition, lease_id)
    SELECT e.seq, 1000 + row_number() OVER (ORDER BY l.id) - 1, c.id, c.version, 'revoke', l.id
    FROM pair_leases l JOIN claims c ON c.id = l.claim_id JOIN evidence_events e ON e.command_id = NEW.command_id
    WHERE l.status = 'pending' AND (l.claim_id IN (SELECT claim_id FROM import_selections WHERE plan_id = NEW.plan_id)
        OR l.repeat_of IN (SELECT receipt_seq FROM event_targets WHERE event_seq = e.seq AND transition IN ('withdraw', 'supersede')));
    UPDATE pair_leases SET status = 'revoked' WHERE id IN (SELECT lease_id FROM event_targets
        WHERE event_seq = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id) AND transition = 'revoke');
    UPDATE session_state SET version = version + 1, current_lease = NULL WHERE current_lease IN (
        SELECT lease_id FROM event_targets WHERE event_seq = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id) AND transition = 'revoke');
    UPDATE claims SET version = version + 1, current_lease = NULL,
        selected_receipt = CASE WHEN id IN (SELECT claim_id FROM import_selections WHERE plan_id = NEW.plan_id)
            THEN (SELECT r.seq FROM import_selections t LEFT JOIN vote_receipts r ON r.acknowledgment = t.selected_acknowledgment
                WHERE t.plan_id = NEW.plan_id AND t.claim_id = claims.id) ELSE selected_receipt END
    WHERE id IN (SELECT claim_id FROM event_targets WHERE event_seq = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id));
    INSERT INTO refresh_coverage(claim_id) SELECT claim_id FROM import_selections WHERE plan_id = NEW.plan_id;
    INSERT INTO session_cases(session_id, case_key, receipt_seq)
    SELECT r.session_id, r.case_key, max(r.seq) FROM vote_receipts r JOIN receipt_state s ON s.receipt_seq = r.seq
    WHERE r.session_id IN (SELECT session_id FROM import_session_bindings WHERE plan_id = NEW.plan_id)
        AND s.withdrawn IS NULL GROUP BY r.session_id, r.case_key
    ON CONFLICT(session_id, case_key) DO UPDATE SET receipt_seq = excluded.receipt_seq;

    UPDATE import_sources SET completed_at = NEW.now_ms
    WHERE id = (SELECT source_id FROM import_plans WHERE id = NEW.plan_id) AND completed_at IS NULL;
    INSERT INTO import_applications(plan_id, event_seq)
    SELECT NEW.plan_id, seq FROM evidence_events WHERE command_id = NEW.command_id;
    UPDATE family_state SET last_evidence_seq = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id),
        revision = (SELECT seq FROM evidence_events WHERE command_id = NEW.command_id)
    WHERE family = (SELECT s.family FROM import_sources s JOIN import_plans p ON p.source_id = s.id WHERE p.id = NEW.plan_id);
    DELETE FROM import_execution WHERE plan_id = NEW.plan_id;
END;
