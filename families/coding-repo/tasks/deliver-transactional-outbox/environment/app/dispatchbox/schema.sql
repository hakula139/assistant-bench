PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0)
);
