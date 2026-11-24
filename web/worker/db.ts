import {
  DummyDriver,
  Kysely,
  SqliteAdapter,
  SqliteIntrospector,
  SqliteQueryCompiler,
  type Compilable,
} from 'kysely';

import type { Database } from './schema';

/** Compiler-only Kysely query builder for the D1 SQLite vote store. */
export const queries = new Kysely<Database>({
  dialect: {
    createAdapter: () => new SqliteAdapter(),
    createDriver: () => new DummyDriver(),
    createIntrospector: (db) => new SqliteIntrospector(db),
    createQueryCompiler: () => new SqliteQueryCompiler(),
  },
});

export interface RunResult<R> {
  results: R[];
  changes: number;
}

export type BatchResults<T extends readonly Compilable[]> = {
  -readonly [K in keyof T]: T[K] extends Compilable<infer R> ? RunResult<R> : never;
};

function prepare<R>(db: D1Database, query: Compilable<R>): D1PreparedStatement {
  const { sql, parameters } = query.compile();
  return db.prepare(sql).bind(...parameters);
}

/** Runs a compiled query and returns all matching rows. */
export async function all<R>(db: D1Database, query: Compilable<R>): Promise<R[]> {
  const { results } = await prepare(db, query).all<R>();
  return results;
}

/** Runs a compiled query and returns the first matching row, or null. */
export async function first<R>(db: D1Database, query: Compilable<R>): Promise<R | null> {
  return prepare(db, query).first<R>();
}

/** Runs a compiled write (or returning write) and returns its rows and affected row count. */
export async function run<R>(db: D1Database, query: Compilable<R>): Promise<RunResult<R>> {
  const { results, meta } = await prepare(db, query).all<R>();
  return { results, changes: meta.changes };
}

/** Runs compiled queries in one transactional D1 batch with per-slot inferred result types. */
export async function batch<const T extends readonly Compilable[]>(
  db: D1Database,
  items: T,
): Promise<BatchResults<T>>;
export async function batch<R>(
  db: D1Database,
  items: readonly Compilable<R>[],
): Promise<RunResult<R>[]>;
export async function batch(
  db: D1Database,
  items: readonly Compilable[],
): Promise<RunResult<unknown>[]> {
  if (items.length === 0) {
    return [];
  }
  const results = await db.batch(items.map((query) => prepare(db, query)));
  return results.map(({ results: rows, meta }) => ({ results: rows, changes: meta.changes }));
}
