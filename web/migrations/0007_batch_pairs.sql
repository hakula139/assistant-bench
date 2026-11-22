CREATE TABLE batch_pairs (
    id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL REFERENCES actors (id),
    family TEXT NOT NULL,
    publication_id TEXT NOT NULL,
    case_key TEXT NOT NULL,
    a_output TEXT NOT NULL,
    b_output TEXT NOT NULL,
    issued_at INTEGER NOT NULL,
    position INTEGER NOT NULL,
    FOREIGN KEY (publication_id, a_output) REFERENCES outputs (publication_id, output_key),
    FOREIGN KEY (publication_id, b_output) REFERENCES outputs (publication_id, output_key)
) STRICT;

CREATE INDEX batch_pairs_actor ON batch_pairs (actor_id, family, publication_id);

CREATE VIEW pending_pairs AS
    SELECT id, actor_id, family, publication_id, case_key, a_output, b_output, issued_at,
        0 AS position, 0 AS batched
    FROM pairs
    UNION ALL
    SELECT id, actor_id, family, publication_id, case_key, a_output, b_output, issued_at,
        position, 1 AS batched
    FROM batch_pairs;

CREATE TRIGGER pending_pairs_delete INSTEAD OF DELETE ON pending_pairs
BEGIN
    DELETE FROM pairs WHERE id = OLD.id AND OLD.batched = 0;
    DELETE FROM batch_pairs WHERE id = OLD.id AND OLD.batched = 1;
END;

CREATE TRIGGER active_clear_pending AFTER UPDATE OF publication_id ON active
WHEN NEW.publication_id <> OLD.publication_id
BEGIN
    DELETE FROM pending_pairs WHERE family = NEW.family;
END;

CREATE TRIGGER votes_clear_pending AFTER INSERT ON votes
BEGIN
    DELETE FROM pending_pairs
    WHERE actor_id = NEW.actor_id AND family = NEW.family AND case_key = NEW.case_key
        AND EXISTS (
            SELECT 1 FROM outputs AS a
            JOIN outputs AS b ON b.publication_id = a.publication_id
            WHERE a.publication_id = pending_pairs.publication_id
                AND a.output_key = pending_pairs.a_output AND b.output_key = pending_pairs.b_output
                AND min(a.system, b.system) = min(NEW.a_system, NEW.b_system)
                AND max(a.system, b.system) = max(NEW.a_system, NEW.b_system)
        );
END;
