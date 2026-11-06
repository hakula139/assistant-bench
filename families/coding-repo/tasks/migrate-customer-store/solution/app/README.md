# Customer store

A SQLite customer/order application on the version-2 schema, which migrates version-1 stores and keeps a stable customer JSON API and report.

## Public interface

The package exports `Customer(id: int, display_name: str)`, `Store`, `CustomerNotFoundError`, `MigrationError`, `SchemaVersionError`, `open_store`, `migrate`, `customer_json` and `customer_report`.

`open_store(path: Path) -> Store` initializes an empty store at version 2, migrates documented version 1 in one transaction, and opens version 2 without rewriting its data. PRAGMA user_version holds the version, and 0 marks an empty store. A version other than 0, 1 or 2 raises SchemaVersionError, whose version attribute is the rejected integer, before writes. NULL legacy names raise MigrationError and preserve the schema, version and every row. Empty strings and Unicode names are valid.

`migrate(connection: sqlite3.Connection) -> None` initializes or migrates the database of an idle connection with foreign keys enabled, as `open_store` does, and leaves version 2 unchanged. A migration that fails, or whose process dies partway, leaves the version-1 store unchanged, and a failed migration leaves the connection usable.

`Store` exposes connection, `create_customer(customer_id: int, display_name: str) -> Customer`, `get_customer(customer_id: int) -> Customer`, `create_order(order_id: int, customer_id: int, amount_cents: int) -> None`, `customers() -> list[Customer]`, and close(), plus context-manager support. Each write commits on its own. Missing customers raise CustomerNotFoundError, whose customer_id attribute identifies the missing integer. Customer lists sort by ID.

`customer_json(store: Store, customer_id: int) -> dict[str, int | str]` returns the JSON shape `{"id": 1, "name": "Ada"}`.

`customer_report(store: Store)` returns `{"customers": [{"id": 1, "name": "Ada", "orders": 2, "total_cents": 500}], "total_cents": 500}`, sorting customers by ID and including those with no orders. Totals are integer cents. `python -m shopstore report --db PATH` emits the same report JSON, exits 0 on success and uses stderr/nonzero on failure.

## Persisted format

schema-v1.sql defines the complete supported legacy schema, whose nullable customers.full_name column can hold a NULL name that fails migration. Version 2 replaces that column with a required display_name column holding the exact name values, under the same customer IDs and related orders. Both versions keep the customers_name index on the name column, the orders_customer_id index, and valid foreign keys. Orders have id, customer_id and amount_cents. Tests may inspect these public names through a separate SQLite connection. Other legacy schemas are unsupported.
