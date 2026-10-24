CREATE VIEW annotation_authority AS
SELECT r.actor_id, r.seq AS receipt_seq, NULL AS source_id, NULL AS original_id
FROM vote_receipts r WHERE r.origin = 'served'
UNION ALL
SELECT s.owner_id, NULL, s.id, raw.original_id FROM import_sources s
JOIN import_rows raw ON raw.source_id = s.id AND raw.row_kind = 'receipt';

CREATE VIEW deliver_annotation AS
SELECT NULL AS actor_id, NULL AS receipt_seq, NULL AS source_id,
    NULL AS original_id, NULL AS note WHERE 0;

CREATE TRIGGER deliver_annotation_insert INSTEAD OF INSERT ON deliver_annotation
BEGIN
    SELECT CASE WHEN (SELECT mode FROM deployment WHERE id = 1) != 'local'
        THEN RAISE(ABORT, 'private_material') END;
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM annotation_authority
        WHERE actor_id = NEW.actor_id AND receipt_seq IS NEW.receipt_seq
            AND source_id IS NEW.source_id AND original_id IS NEW.original_id)
        THEN RAISE(ABORT, 'annotation_target') END;
    SELECT CASE WHEN NEW.note IS NULL OR length(NEW.note) > 500
        THEN RAISE(ABORT, 'annotation_invalid') END;
    SELECT CASE WHEN EXISTS (SELECT 1 FROM private_annotations
        WHERE receipt_seq IS NEW.receipt_seq AND source_id IS NEW.source_id AND original_id IS NEW.original_id
            AND redacted_at IS NULL AND note IS NOT NEW.note)
        THEN RAISE(ABORT, 'conflict') END;
    INSERT INTO private_annotations(receipt_seq, source_id, original_id, note)
    SELECT NEW.receipt_seq, NEW.source_id, NEW.original_id, NEW.note
    WHERE NOT EXISTS (SELECT 1 FROM private_annotations WHERE receipt_seq IS NEW.receipt_seq
        AND source_id IS NEW.source_id AND original_id IS NEW.original_id);
END;

CREATE VIEW redact_annotation AS
SELECT NULL AS actor_id, NULL AS receipt_seq, NULL AS source_id,
    NULL AS original_id, NULL AS now_ms WHERE 0;

CREATE TRIGGER redact_annotation_insert INSTEAD OF INSERT ON redact_annotation
BEGIN
    SELECT CASE WHEN (SELECT mode FROM deployment WHERE id = 1) != 'local'
        THEN RAISE(ABORT, 'private_material') END;
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM annotation_authority
        WHERE actor_id = NEW.actor_id AND receipt_seq IS NEW.receipt_seq
            AND source_id IS NEW.source_id AND original_id IS NEW.original_id)
        THEN RAISE(ABORT, 'annotation_target') END;
    INSERT INTO private_annotations(receipt_seq, source_id, original_id, redacted_at)
    SELECT NEW.receipt_seq, NEW.source_id, NEW.original_id, NEW.now_ms
    WHERE NOT EXISTS (SELECT 1 FROM private_annotations WHERE receipt_seq IS NEW.receipt_seq
        AND source_id IS NEW.source_id AND original_id IS NEW.original_id);
    UPDATE private_annotations SET note = NULL, redacted_at = NEW.now_ms
    WHERE receipt_seq IS NEW.receipt_seq AND source_id IS NEW.source_id
        AND original_id IS NEW.original_id AND redacted_at IS NULL;
END;
