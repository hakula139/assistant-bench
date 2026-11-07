CREATE TABLE publication_pair_cases (
    publication_id TEXT NOT NULL REFERENCES publications (id),
    low_system TEXT NOT NULL,
    high_system TEXT NOT NULL,
    case_keys TEXT NOT NULL CHECK (json_valid(case_keys)),
    PRIMARY KEY (publication_id, low_system, high_system)
) STRICT;

CREATE INDEX outputs_passing_samples
    ON outputs (publication_id, case_key, system, output_key, content_digest)
    WHERE verdict = 'pass';

INSERT INTO publication_pair_cases (publication_id, low_system, high_system, case_keys)
SELECT publication_id, low_system, high_system, json_group_array(case_key)
FROM (
    SELECT DISTINCT a.publication_id, a.system AS low_system, b.system AS high_system, a.case_key
    FROM outputs AS a
    JOIN outputs AS b
        ON b.publication_id = a.publication_id AND b.case_key = a.case_key
    WHERE a.verdict = 'pass' AND b.verdict = 'pass'
        AND a.system < b.system AND a.content_digest <> b.content_digest
    ORDER BY a.publication_id, low_system, high_system, a.case_key
)
GROUP BY publication_id, low_system, high_system;
