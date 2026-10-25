PRAGMA foreign_keys = ON;
CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    full_name TEXT
);
CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    amount_cents INTEGER NOT NULL
);
CREATE INDEX customers_name ON customers(full_name);
CREATE INDEX orders_customer_id ON orders(customer_id);
PRAGMA user_version = 1;
