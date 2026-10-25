# Customer store

The baseline is a version-1 SQLite customer/order application. Upgrade its internal name model and persistence together while preserving the serialized API and report.

## Public interface after the change

The package exports `Customer(id: int, display_name: str)`, `Store`, `CustomerNotFoundError`, `MigrationError`, `SchemaVersionError`, `open_store`, `migrate`, `customer_json` and `customer_report`.

`open_store(path: Path) -> Store` initializes an empty store at version 2, migrates documented version 1 atomically, and opens version 2 without rewriting its data. PRAGMA user_version holds the version. Unknown nonzero versions raise SchemaVersionError, whose version attribute is the rejected integer, before writes. NULL legacy names raise MigrationError and preserve the schema, version and every row. Empty strings and Unicode names are valid. No deprecated Python aliases are needed.

`migrate(connection: sqlite3.Connection) -> None` provides the same migration on an idle connection with foreign keys enabled. A failed migration rolls back and leaves the connection usable.

`Store` exposes connection, `create_customer(customer_id: int, display_name: str) -> Customer`, `get_customer(customer_id: int) -> Customer`, `create_order(order_id: int, customer_id: int, amount_cents: int) -> None`, `customers() -> list[Customer]`, and close(), plus context-manager support. Writes commit independently. Missing customers raise CustomerNotFoundError, whose customer_id attribute identifies the missing integer. Customer lists sort by ID.

`customer_json(store: Store, customer_id: int) -> dict[str, int | str]` retains the JSON shape `{"id": 1, "name": "Ada"}`.

`customer_report(store: Store)` returns `{"customers": [{"id": 1, "name": "Ada", "orders": 2, "total_cents": 500}], "total_cents": 500}`, sorting customers by ID and including those with no orders. Totals are integer cents. `python -m shopstore report --db PATH` emits the same report JSON, exits 0 on success and uses stderr/nonzero on failure.

## Persisted format

schema-v1.sql defines the complete supported legacy schema. The customers.full_name column is nullable, allowing the failed-migration case. Version 2 has the same customer IDs and required display_name, preserving exact name values and related orders. Preserve customers_name and orders_customer_id indexes and foreign-key validity. Orders have id, customer_id and amount_cents. Tests may inspect these public names through a separate SQLite connection.

The baseline creates and opens version 1 and consumers use Customer.full_name. Implement the required version-2 model and migration. Arbitrary legacy schemas are outside the task.
