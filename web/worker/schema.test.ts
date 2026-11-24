import { env } from 'cloudflare:workers';
import { describe, expect, it } from 'vitest';

interface ParsedColumn {
  affinity: 'INTEGER' | 'TEXT';
  nullable: boolean;
  generated: boolean;
}

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

function classifyType(rawType: string): ParsedColumn {
  let type = rawType.trim();
  let generated = false;
  if (type.startsWith('Generated<') && type.endsWith('>')) {
    generated = true;
    type = type.slice('Generated<'.length, -1).trim();
  }

  const parts = type
    .split('|')
    .map((part) => part.trim())
    .filter((part) => part !== '');
  const nullable = parts.includes('null');
  const nonNull = parts.filter((part) => part !== 'null');
  const isInteger =
    nonNull.length > 0 && nonNull.every((part) => part === 'number' || /^\d+$/.test(part));
  return {
    affinity: isInteger ? 'INTEGER' : 'TEXT',
    nullable,
    generated,
  };
}

function parseDatabaseSchema(source: string): Map<string, Map<string, ParsedColumn>> {
  const withoutComments = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*/g, '');
  const start = withoutComments.indexOf('export interface Database {');
  if (start === -1) {
    throw new Error('Could not locate export interface Database in schema.ts.');
  }
  const body = withoutComments.slice(start + 'export interface Database {'.length);
  const tables = new Map<string, Map<string, ParsedColumn>>();
  const tablePattern = /(\w+)\s*:\s*\{([\s\S]*?)\};/g;
  for (const tableMatch of body.matchAll(tablePattern)) {
    const [, tableName, fields] = tableMatch;
    const columns = new Map<string, ParsedColumn>();
    const fieldPattern = /(\w+)\s*:\s*([^;]+);/g;
    for (const fieldMatch of fields.matchAll(fieldPattern)) {
      const [, columnName, rawType] = fieldMatch;
      columns.set(columnName, classifyType(rawType));
    }
    tables.set(tableName, columns);
  }
  return tables;
}

describe('schema.ts parity with D1 migrations', () => {
  it('matches every table, view, column, type, nullability, and default', async () => {
    const schema = parseDatabaseSchema(env.TEST_SCHEMA);
    const { results: objects } = await env.DB.prepare(
      `SELECT name, type, sql FROM sqlite_master
       WHERE type IN ('table', 'view')
         AND name NOT LIKE 'sqlite_%'
         AND name NOT LIKE '_cf_%'
         AND name <> 'd1_migrations'
       ORDER BY name`,
    ).all<SqliteMasterRow>();

    expect([...schema.keys()].sort()).toEqual(objects.map(({ name }) => name));

    for (const { name, type, sql } of objects) {
      const columns = schema.get(name);
      if (columns === undefined) {
        throw new Error(`Missing ${name} in Database.`);
      }

      const { results: info } = await env.DB.prepare(
        `PRAGMA table_info("${name}")`,
      ).all<TableInfoRow>();
      expect([...columns.keys()].sort(), `columns of ${name}`).toEqual(
        info.map((col) => col.name).sort(),
      );

      if (type === 'table') {
        const autoincrement = /\bAUTOINCREMENT\b/i.test(sql);
        for (const col of info) {
          const parsed = columns.get(col.name);
          if (parsed === undefined) {
            throw new Error(`Missing ${name}.${col.name} in Database.`);
          }
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
