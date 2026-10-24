"""Inspect the native owner's migration journal before domain initialization."""

from textwrap import dedent
from typing import Annotated, Self

from pydantic import Field, model_validator

from assistant_bench_arena.ledger_models import Statement
from assistant_bench_arena.models import Record
from assistant_bench_arena.transport import Transport


JOURNAL_TABLE = 'd1_migrations'
JOURNAL_DDL = dedent(
    f"""
    CREATE TABLE IF NOT EXISTS {JOURNAL_TABLE} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        digest TEXT NOT NULL CHECK (
            length(digest) = 64 AND digest NOT GLOB '*[^0-9a-f]*')
    ) STRICT;
    """
).strip()


class AppliedMigration(Record):
    id: Annotated[int, Field(strict=True, gt=0)]
    name: Annotated[str, Field(pattern=r'^[0-9]{4}_[a-z0-9_]+\.sql$')]
    digest: Annotated[str, Field(pattern='^[0-9a-f]{64}$')]
    """SHA-256 of the exact numbered migration file bytes."""


class MigrationStatus(Record):
    journal_present: bool
    schema_empty: bool
    """No user schema objects other than the migration journal."""

    applied: tuple[AppliedMigration, ...]

    @model_validator(mode='after')
    def ordered_history(self) -> Self:
        ids = tuple(row.id for row in self.applied)
        names = tuple(row.name for row in self.applied)

        if (
            ids != tuple(sorted(set(ids)))
            or len(names) != len(set(names))
            or (self.applied and not self.journal_present)
        ):
            raise ValueError('The migration journal must have unique ordered history.')

        return self


async def inspect_migrations(transport: Transport) -> MigrationStatus:
    """Read metadata through the sole owner without creating any schema objects."""
    objects = await transport.query(
        Statement(
            sql="""
                SELECT name, type FROM sqlite_schema
                WHERE substr(name, 1, 7) != 'sqlite_'
                    AND substr(name, 1, 4) != '_cf_'
                """
        )
    )
    journal = [row for row in objects if row['name'] == JOURNAL_TABLE]
    schema_empty = all(row['name'] == JOURNAL_TABLE for row in objects)

    if not journal:
        return MigrationStatus(
            journal_present=False, schema_empty=schema_empty, applied=()
        )

    if journal != [{'name': JOURNAL_TABLE, 'type': 'table'}]:
        raise ValueError('The migration journal must be a native table.')

    columns = await transport.query(
        Statement(sql=f'PRAGMA table_info({JOURNAL_TABLE})')
    )
    shape = {
        str(column['name']): (column['type'], column['notnull'], column['pk'])
        for column in columns
    }

    if shape != {
        'id': ('INTEGER', 0, 1),
        'name': ('TEXT', 1, 0),
        'applied_at': ('TEXT', 1, 0),
        'digest': ('TEXT', 1, 0),
    }:
        raise ValueError('The migration journal has an unsupported schema.')

    rows = await transport.query(
        Statement(sql=f'SELECT id, name, digest FROM {JOURNAL_TABLE} ORDER BY id')
    )
    return MigrationStatus(
        journal_present=True,
        schema_empty=schema_empty,
        applied=tuple(AppliedMigration.model_validate(row) for row in rows),
    )
