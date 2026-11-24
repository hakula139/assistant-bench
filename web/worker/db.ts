import {
  DummyDriver,
  Kysely,
  SqliteAdapter,
  SqliteIntrospector,
  SqliteQueryCompiler,
  type Compilable,
  type DeleteResult,
  type InsertResult,
  type JoinBuilder,
  type UpdateResult,
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

export const PAIR_COLUMNS = [
  'id',
  'actor_id',
  'family',
  'publication_id',
  'case_key',
  'a_output',
  'b_output',
  'issued_at',
] as const;

type RowOf<R> = Exclude<R, InsertResult | UpdateResult | DeleteResult>;

interface RunResult<R> {
  results: RowOf<R>[];
  changes: number;
}

type BatchResults<T extends readonly Compilable[]> = {
  -readonly [K in keyof T]: T[K] extends Compilable<infer R> ? RunResult<R> : never;
};

function prepare(db: D1Database, query: Compilable): D1PreparedStatement {
  const { sql, parameters } = query.compile();
  return db.prepare(sql).bind(...parameters);
}

export async function all<R>(db: D1Database, query: Compilable<R>): Promise<R[]> {
  const { results } = await prepare(db, query).all<R>();
  return results;
}

export async function first<R>(db: D1Database, query: Compilable<R>): Promise<R | null> {
  return prepare(db, query).first<R>();
}

export async function run<R>(db: D1Database, query: Compilable<R>): Promise<RunResult<R>> {
  const { results, meta } = await prepare(db, query).all<RowOf<R>>();
  return { results, changes: meta.changes };
}

export async function batch<const T extends readonly Compilable[]>(
  db: D1Database,
  items: T,
): Promise<BatchResults<T>>;
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

export function onActivePair<
  DB extends {
    active: Database['active'];
    pairs: { family: string | null; publication_id: string | null };
  },
>(join: JoinBuilder<DB, 'active' | 'pairs'>) {
  return join
    .onRef('active.family', '=', 'pairs.family')
    .onRef('active.publication_id', '=', 'pairs.publication_id');
}

export function onPairOutputA<
  DB extends {
    a: Database['outputs'];
    pairs: Pick<Database['pending_pairs'], 'publication_id' | 'a_output'>;
  },
>(join: JoinBuilder<DB, 'a' | 'pairs'>) {
  return join
    .onRef('a.publication_id', '=', 'pairs.publication_id')
    .onRef('a.output_key', '=', 'pairs.a_output');
}

export function onPairOutputB<
  DB extends {
    b: Database['outputs'];
    pairs: Pick<Database['pending_pairs'], 'publication_id' | 'b_output'>;
  },
>(join: JoinBuilder<DB, 'b' | 'pairs'>) {
  return join
    .onRef('b.publication_id', '=', 'pairs.publication_id')
    .onRef('b.output_key', '=', 'pairs.b_output');
}

export function upsertPair(
  expression: Parameters<ReturnType<typeof queries.insertInto<'pairs'>>['expression']>[0],
  replaceChangedPublication = false,
) {
  return queries
    .insertInto('pairs')
    .columns(PAIR_COLUMNS)
    .expression(expression)
    .onConflict((oc) => {
      const update = oc.columns(['actor_id', 'family']).doUpdateSet((eb) => ({
        id: eb.ref('excluded.id'),
        publication_id: eb.ref('excluded.publication_id'),
        case_key: eb.ref('excluded.case_key'),
        a_output: eb.ref('excluded.a_output'),
        b_output: eb.ref('excluded.b_output'),
        issued_at: eb.ref('excluded.issued_at'),
      }));
      return replaceChangedPublication
        ? update.whereRef('pairs.publication_id', '<>', 'excluded.publication_id')
        : update;
    });
}
