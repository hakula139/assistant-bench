"""Atomic current-schema initialization through the application's sole owner."""

from assistant_bench_arena.ledger_models import (
    BootstrapConfiguration,
    DeploymentStatus,
    Statement,
)
from assistant_bench_arena.serialization import canonical
from assistant_bench_arena.transport import Transport


SCHEMA_ID = 'arena-v1'


def parse_schema(source: str) -> tuple[Statement, ...]:
    """Split terminated SQL on native Python without opening a database."""
    import sqlite3

    statements: list[Statement] = []
    pending = ''

    for character in source:
        pending += character

        if character == ';' and sqlite3.complete_statement(pending):
            statements.append(Statement(sql=pending))
            pending = ''

    if pending.strip():
        raise ValueError('The schema ends with an unterminated SQL statement.')

    return tuple(statements)


def bootstrap_statements(config: BootstrapConfiguration) -> tuple[Statement, ...]:
    """Append the immutable profile marker last in the schema's native batch."""
    return (
        Statement(
            sql="""
                INSERT INTO deployment(id, schema_id, namespace, quota_body,
                    attribution_ms) VALUES (1, ?, ?, ?, ?)
                """,
            params=(
                SCHEMA_ID,
                config.namespace,
                canonical([row.model_dump(mode='json') for row in config.quotas]),
                config.attribution_ms,
            ),
        ),
    )


async def inspect_schema(transport: Transport) -> DeploymentStatus | None:
    """Return the current profile, or None only for an empty native database."""
    objects = await transport.query(
        Statement(
            sql="""
                SELECT name, type FROM sqlite_schema
                WHERE substr(name, 1, 7) != 'sqlite_'
                    AND substr(name, 1, 4) != '_cf_'
                """
        )
    )

    if not objects:
        return None

    marker = [row for row in objects if row['name'] == 'deployment']

    if marker != [{'name': 'deployment', 'type': 'table'}]:
        raise ValueError('The nonempty database has no current schema marker.')

    columns = await transport.query(Statement(sql='PRAGMA table_info(deployment)'))

    if {str(column['name']) for column in columns} != {
        'id',
        'schema_id',
        'namespace',
        'quota_body',
        'attribution_ms',
    }:
        raise ValueError('The database has an incompatible schema marker.')

    rows = await transport.query(
        Statement(
            sql="""
                SELECT json_object('schema_id', schema_id, 'namespace', namespace,
                    'quotas', json(quota_body),
                    'attribution_ms', attribution_ms) AS body FROM deployment
                """
        )
    )

    if len(rows) != 1 or not isinstance(rows[0]['body'], str):
        raise ValueError('The nonempty database has no unique initialized profile.')

    return DeploymentStatus.model_validate_json(rows[0]['body'])
