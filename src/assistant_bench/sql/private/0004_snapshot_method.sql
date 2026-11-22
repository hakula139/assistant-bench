ALTER TABLE vote_snapshots
ADD COLUMN method TEXT CHECK (method = 'expected_net');

CREATE TRIGGER snapshots_keep_method BEFORE UPDATE OF method ON vote_snapshots
WHEN NEW.method IS NOT OLD.method
BEGIN
    SELECT RAISE(ABORT, 'Vote snapshot method is immutable');
END;
