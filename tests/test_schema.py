from assistant_bench import schema
from support import private_database


class TestSchemaParity:
    def test_metadata_matches_sql_migrations(self) -> None:
        with private_database() as database:
            sql_tables = {
                str(row['name'])
                for row in database.execute(
                    'SELECT name FROM sqlite_master '
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                ).rows
            }
            assert set(schema.Base.metadata.tables) == sql_tables

            for table_name, table in schema.Base.metadata.tables.items():
                info = database.execute(f'PRAGMA table_info({table_name})').rows
                sql_columns = {str(row['name']): row for row in info}
                assert set(table.columns.keys()) == set(sql_columns)

                sql_pk = [
                    str(row['name'])
                    for row in sorted(
                        (row for row in info if int(str(row['pk'])) > 0),
                        key=lambda row: int(str(row['pk'])),
                    )
                ]
                sa_pk = [col.name for col in table.primary_key.columns]
                assert sa_pk == sql_pk, f'{table_name} primary key mismatch'

                for col_name, col in table.columns.items():
                    row = sql_columns[col_name]
                    assert col.type.__class__.__name__.upper() == str(row['type']), (
                        f'{table_name}.{col_name} type mismatch'
                    )
                    is_pk = int(str(row['pk'])) > 0
                    if is_pk:
                        assert col.primary_key
                        assert not col.nullable
                    else:
                        sql_not_null = bool(row['notnull'])
                        assert (not col.nullable) == sql_not_null, (
                            f'{table_name}.{col_name} NOT NULL mismatch'
                        )
