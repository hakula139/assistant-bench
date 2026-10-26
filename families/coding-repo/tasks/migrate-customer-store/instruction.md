# Upgrade the persisted customer name model atomically

The application in `/app` reads a version-1 SQLite customer and order database, uses `Customer.full_name` internally, and exposes a stable customer JSON API and report.

Implement the version-2 contract in `README.md`. Empty stores start at version 2. Version-1 stores migrate atomically from `full_name` to a required `display_name` while preserving exact names, IDs, orders, indexes and foreign keys. A `NULL` legacy name or an unsupported future version must leave the database unchanged. Update the repository, API and reporting callers together without retaining deprecated Python aliases.

Keep serialized name keys and report totals stable. Use the supplied legacy SQL format, and add focused regression tests for migrated data and rollback. Use only Python 3.14's standard library.
