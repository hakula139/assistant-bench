CREATE TABLE pair_vote_counts (
    family TEXT NOT NULL,
    low_system TEXT NOT NULL,
    high_system TEXT NOT NULL,
    votes INTEGER NOT NULL CHECK (votes >= 0),
    PRIMARY KEY (family, low_system, high_system)
) STRICT;

CREATE INDEX votes_counted_family ON votes (family, id) WHERE exclusion IS NULL;

INSERT INTO pair_vote_counts (family, low_system, high_system, votes)
SELECT family, min(a_system, b_system), max(a_system, b_system), count(*)
FROM votes WHERE exclusion IS NULL
GROUP BY family, min(a_system, b_system), max(a_system, b_system);

CREATE TRIGGER votes_count_insert AFTER INSERT ON votes WHEN NEW.exclusion IS NULL
BEGIN
    INSERT INTO pair_vote_counts (family, low_system, high_system, votes)
    VALUES (NEW.family, min(NEW.a_system, NEW.b_system), max(NEW.a_system, NEW.b_system), 1)
    ON CONFLICT (family, low_system, high_system) DO UPDATE SET votes = votes + 1;
END;

CREATE TRIGGER votes_count_delete AFTER DELETE ON votes WHEN OLD.exclusion IS NULL
BEGIN
    UPDATE pair_vote_counts SET votes = votes - 1
    WHERE family = OLD.family
        AND low_system = min(OLD.a_system, OLD.b_system)
        AND high_system = max(OLD.a_system, OLD.b_system);
END;

CREATE TRIGGER votes_count_exclude AFTER UPDATE OF exclusion ON votes
WHEN OLD.exclusion IS NULL AND NEW.exclusion IS NOT NULL
BEGIN
    UPDATE pair_vote_counts SET votes = votes - 1
    WHERE family = OLD.family
        AND low_system = min(OLD.a_system, OLD.b_system)
        AND high_system = max(OLD.a_system, OLD.b_system);
END;

CREATE TRIGGER votes_count_restore AFTER UPDATE OF exclusion ON votes
WHEN OLD.exclusion IS NOT NULL AND NEW.exclusion IS NULL
BEGIN
    INSERT INTO pair_vote_counts (family, low_system, high_system, votes)
    VALUES (NEW.family, min(NEW.a_system, NEW.b_system), max(NEW.a_system, NEW.b_system), 1)
    ON CONFLICT (family, low_system, high_system) DO UPDATE SET votes = votes + 1;
END;
