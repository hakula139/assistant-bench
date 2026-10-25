CREATE TABLE IF NOT EXISTS ledger (
    external_id TEXT PRIMARY KEY,
    amount_cents INTEGER NOT NULL,
    memo TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    checkpoint INTEGER NOT NULL DEFAULT 0 CHECK (checkpoint >= 0)
);
