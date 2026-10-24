PRAGMA foreign_keys = ON;

CREATE TABLE deployment (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    namespace TEXT NOT NULL UNIQUE,
    mode TEXT NOT NULL CHECK (mode IN ('local', 'public')),
    local_key_id TEXT,
    quota_body TEXT NOT NULL CHECK (json_valid(quota_body) AND json_type(quota_body) = 'array'),
    attribution_ms INTEGER NOT NULL CHECK (attribution_ms BETWEEN 0 AND 7776000000),
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    CHECK ((mode = 'local' AND local_key_id IS NOT NULL AND length(local_key_id) > 0)
        OR (mode = 'public' AND local_key_id IS NULL))
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

CREATE TRIGGER deployment_initialize AFTER INSERT ON deployment
BEGIN
    INSERT INTO quota_policies(operation, scope, window_ms, allowance)
    SELECT json_extract(value, '$.operation'), json_extract(value, '$.scope'),
        json_extract(value, '$.window_ms'), json_extract(value, '$.limit')
    FROM json_each(NEW.quota_body);
END;

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

CREATE VIEW seal_evaluation AS
SELECT NULL AS evaluation_id, NULL AS now_ms WHERE 0;

CREATE TRIGGER seal_evaluation_insert INSTEAD OF INSERT ON seal_evaluation
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

    SELECT CASE WHEN (SELECT mode FROM deployment) = 'public' AND (
        EXISTS (SELECT 1 FROM evaluation_cases
                WHERE evaluation_id = NEW.evaluation_id AND source = 'private')
        OR EXISTS (SELECT 1 FROM evaluation_outputs
                   WHERE evaluation_id = NEW.evaluation_id AND approved_public = 0)
    ) THEN RAISE(ABORT, 'private_material') END;

    UPDATE evaluations SET sealed_at = NEW.now_ms WHERE id = NEW.evaluation_id;
END;

CREATE VIEW seal_publication AS
SELECT NULL AS publication_id, NULL AS now_ms WHERE 0;

CREATE TRIGGER seal_publication_insert INSTEAD OF INSERT ON seal_publication
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

    SELECT CASE WHEN (SELECT mode FROM deployment) = 'public' AND EXISTS (
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
