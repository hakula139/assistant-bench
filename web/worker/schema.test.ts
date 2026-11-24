import { env } from 'cloudflare:workers';
import { describe, expect, test } from 'vitest';

interface SqliteMasterRow {
  name: string;
  type: 'table' | 'view';
  sql: string;
}

interface TableInfoRow {
  name: string;
  type: string;
  notnull: 0 | 1;
  dflt_value: string | null;
  pk: number;
}

describe('schema.ts parity with D1 migrations', () => {
  test('matches table columns, types, nullability, and defaults, and view names and columns', async () => {
    const schema = env.TEST_SCHEMA;
    const { results: objects } = await env.DB.prepare(
      `SELECT name, type, sql FROM sqlite_master
       WHERE type IN ('table', 'view')
         AND name NOT LIKE 'sqlite_%'
         AND name NOT LIKE '_cf_%'
         AND name <> 'd1_migrations'
       ORDER BY name`,
    ).all<SqliteMasterRow>();

    expect(Object.keys(schema).sort()).toEqual(objects.map(({ name }) => name));

    for (const { name, type, sql } of objects) {
      const columns = schema[name];
      expect(columns, `Missing ${name} in Database.`).toBeDefined();

      const { results: info } = await env.DB.prepare(
        `PRAGMA table_info("${name}")`,
      ).all<TableInfoRow>();
      expect(Object.keys(columns).sort(), `columns of ${name}`).toEqual(
        info.map((col) => col.name).sort(),
      );

      if (type === 'table') {
        const autoincrement = /\bAUTOINCREMENT\b/i.test(sql);
        for (const col of info) {
          const parsed = columns[col.name];
          expect(parsed, `Missing ${name}.${col.name} in Database.`).toBeDefined();
          expect(parsed.affinity, `${name}.${col.name} type`).toBe(col.type.toUpperCase());
          expect(parsed.nullable, `${name}.${col.name} nullability`).toBe(
            col.notnull === 0 && col.pk === 0,
          );
          expect(parsed.generated, `${name}.${col.name} Generated<>`).toBe(
            col.dflt_value !== null || (col.pk === 1 && autoincrement),
          );
        }
      }
    }
  });
});
