PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0)
);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL UNIQUE REFERENCES orders(order_id),
    idempotency_key TEXT NOT NULL UNIQUE,
    payload BLOB NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0 CHECK (delivered IN (0, 1))
);
