import { env, exports } from 'cloudflare:workers';
import { sql, type Compilable } from 'kysely';
import { expect, vi } from 'vitest';

import type { PairState, PairView } from '@/lib/arena';

import { all, batch, first, onPairOutputA, onPairOutputB, queries } from '../db';

export const ORIGIN = 'http://localhost:8790';
export const WRITING = 'writing-fixture';
export const CODING = 'coding-fixture';
export const FIRST_CASE = '32be94015839ac0131d6874ffcc11c79f78e8059790f678d80fa7008a1286c22';

export interface Browser {
  ip: string;
  cookie: string | null;
}

export const browser = (ip = '192.0.2.1'): Browser => ({ ip, cookie: null });

export async function call(
  from: Browser,
  method: 'GET' | 'POST',
  path: string,
  body?: unknown,
  headers: Record<string, string> = {},
): Promise<Response> {
  const response = await exports.default.fetch(
    new Request(`${ORIGIN}/api${path}`, {
      method,
      headers: {
        'CF-Connecting-IP': from.ip,
        ...(method === 'POST' ? { 'Origin': ORIGIN, 'Content-Type': 'application/json' } : {}),
        ...(from.cookie === null ? {} : { Cookie: from.cookie }),
        ...headers,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
  const set = response.headers.get('Set-Cookie');
  if (set !== null) {
    from.cookie = set.split(';')[0];
  }
  return response;
}

/**
 * Waits, when the rate limiter's current minute has less than `needed` ms left, for the next one,
 * since its windows follow the wall clock and a rollover mid-test resets every count.
 */
export async function freshWindow(needed = 5000): Promise<void> {
  const left = 60_000 - (Date.now() % 60_000);
  if (left < needed) {
    await new Promise((resolve) => setTimeout(resolve, left));
  }
}

export function stubSiteverify(result: { success: boolean; hostname?: string }) {
  const original = globalThis.fetch;
  return vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
    const url = input instanceof Request ? input.url : String(input);
    return url === 'https://challenges.cloudflare.com/turnstile/v0/siteverify'
      ? Promise.resolve(Response.json(result))
      : original(input, init);
  });
}

export async function admitted(ip?: string): Promise<Browser> {
  const from = browser(ip);
  const verify = stubSiteverify({ success: true, hostname: env.TURNSTILE_HOSTNAME });
  const response = await call(from, 'POST', '/actors', { token: 'token' });
  verify.mockRestore();
  if (response.status !== 204) {
    throw new Error(`Admission failed with ${response.status}.`);
  }
  return from;
}

export async function actorOf(from: Browser): Promise<string> {
  const rows = await all(
    env.DB,
    queries.selectFrom('actors').select('id').orderBy('created_at', 'desc'),
  );
  const row = rows.find(({ id }) => from.cookie?.includes(id));
  if (row === undefined) {
    throw new Error('The browser names no actor.');
  }
  return row.id;
}

export async function pairOf(from: Browser, family = WRITING): Promise<PairState> {
  const response = await call(from, 'GET', `/pair?family=${family}`);
  if (response.status !== 200) {
    throw new Error(`The pair request failed with ${response.status}.`);
  }
  return response.json();
}

export async function pendingRow(pair: string) {
  return first(
    env.DB,
    queries
      .selectFrom('pending_pairs as pairs')
      .innerJoin('outputs as a', onPairOutputA)
      .innerJoin('outputs as b', onPairOutputB)
      .select(['pairs.case_key', 'a.system as a_system', 'b.system as b_system', 'pairs.issued_at'])
      .where('pairs.id', '=', pair),
  );
}

export const systemsOf = (row: { a_system: string; b_system: string } | null) =>
  row === null ? null : [row.a_system, row.b_system].sort();

export async function vote(from: Browser, pair: string, choice = 'a') {
  return call(from, 'POST', '/votes', { pair, choice, defects: { a: [], b: [] } });
}

export function duringPlacement(action: () => Promise<void>): void {
  const prepare = env.DB.prepare.bind(env.DB);
  let interrupted = false;
  vi.spyOn(env.DB, 'prepare').mockImplementation((query) => {
    const statement = prepare(query);
    if (query.includes('"system" in (?, ?)')) {
      const bind = statement.bind.bind(statement);
      vi.spyOn(statement, 'bind').mockImplementation((...values) => {
        const bound = bind(...values);
        const allRows = bound.all.bind(bound);
        vi.spyOn(bound, 'all').mockImplementation(async <T>() => {
          const result = await allRows<T>();
          if (!interrupted) {
            interrupted = true;
            await action();
          }
          return result;
        });
        return bound;
      });
    }
    return statement;
  });
}

export function expectIsolation(response: Response, extraCsp: readonly string[] = []): void {
  expect(response.headers.get('Cache-Control')).toBe('private, no-store');
  expect(response.headers.get('Content-Security-Policy')?.split('; ').sort()).toEqual(
    [
      'sandbox',
      "default-src 'none'",
      "style-src 'unsafe-inline'",
      'img-src data:',
      'font-src data:',
      "connect-src 'none'",
      "form-action 'none'",
      "base-uri 'none'",
      "object-src 'none'",
      "frame-src 'none'",
      ...extraCsp,
    ].sort(),
  );
  expect(response.headers.get('X-Frame-Options')).toBe('SAMEORIGIN');
  expect(response.headers.get('X-DNS-Prefetch-Control')).toBe('off');
  expect(response.headers.get('Referrer-Policy')).toBe('no-referrer');
  expect(response.headers.get('X-Content-Type-Options')).toBe('nosniff');
}

/**
 * Publishes and activates a family whose outputs `cases` lists per case and system, with
 * null for a failing output. Equal strings are identical content.
 */
export async function publish(
  family: string,
  cases: Record<string, Record<string, (string | null)[]>>,
  id = `${family}-publication`,
  render: PairView['render'] = 'text',
): Promise<void> {
  const statements: Compilable[] = [
    queries.insertInto('publications').values({
      id,
      family,
      display_name: 'Synthetic',
      aspect: 'writing',
      render,
      defects: '["facts", "tone"]',
      reference: 'acme-large',
      systems: '{}',
      case_count: 0,
      output_count: 0,
      created_at: 0,
    }),
  ];

  for (const [caseKey, systems] of Object.entries(cases)) {
    statements.push(
      queries.insertInto('cases').values({
        publication_id: id,
        case_key: caseKey,
        category: 'reply',
        instruction: `Instruction ${caseKey}`,
        checklist: '[]',
        source: null,
      }),
    );
    for (const [system, contents] of Object.entries(systems)) {
      contents.forEach((content, index) => {
        statements.push(
          queries.insertInto('outputs').values({
            publication_id: id,
            output_key: `${caseKey}/${system}/${index}`,
            case_key: caseKey,
            system,
            fingerprint: `fp-${system}`,
            verdict: content === null ? 'fail' : 'pass',
            errored: 0,
            length: render === 'html' ? null : 1,
            content_digest: content ?? `failed ${caseKey}/${system}/${index}`,
            content,
          }),
        );
      });
    }
  }

  statements.push(
    queries
      .insertInto('publication_pair_cases')
      .columns(['publication_id', 'low_system', 'high_system', 'case_keys'])
      .expression((eb) =>
        eb
          .selectFrom(
            eb
              .selectFrom('outputs as a')
              .innerJoin('outputs as b', (join) =>
                join
                  .onRef('b.publication_id', '=', 'a.publication_id')
                  .onRef('b.case_key', '=', 'a.case_key'),
              )
              .select([
                'a.publication_id',
                'a.system as low_system',
                'b.system as high_system',
                'a.case_key',
              ])
              .distinct()
              .where('a.publication_id', '=', id)
              .where('a.verdict', '=', 'pass')
              .where('b.verdict', '=', 'pass')
              .whereRef('a.system', '<', 'b.system')
              .whereRef('a.content_digest', '<>', 'b.content_digest')
              .orderBy('low_system')
              .orderBy('high_system')
              .orderBy('a.case_key')
              .as('distinct_cases'),
          )
          .select((sub) => [
            'distinct_cases.publication_id',
            'distinct_cases.low_system',
            'distinct_cases.high_system',
            sql<string>`json_group_array(${sub.ref('distinct_cases.case_key')})`.as('case_keys'),
          ])
          .groupBy([
            'distinct_cases.publication_id',
            'distinct_cases.low_system',
            'distinct_cases.high_system',
          ]),
      ),
  );

  statements.push(
    queries
      .insertInto('active')
      .values({ family, publication_id: id, activated_at: 0 })
      .onConflict((oc) =>
        oc.column('family').doUpdateSet((eb) => ({
          publication_id: eb.ref('excluded.publication_id'),
        })),
      ),
  );
  await batch(env.DB, statements);
}

/** Inserts a vote row for the active publication, defaulting to a new actor and no exclusion. */
export async function seedVote(
  family: string,
  caseKey: string,
  systems: readonly [string, string],
  options: { actor?: string; ipTag?: string; recordedAt?: number; exclusion?: string } = {},
) {
  const actor = options.actor ?? crypto.randomUUID();
  await batch(env.DB, [
    queries
      .insertInto('actors')
      .values({ id: actor, created_at: 0, ip_tag: null })
      .onConflict((oc) => oc.doNothing()),
    queries
      .insertInto('votes')
      .columns([
        'pair_id',
        'actor_id',
        'family',
        'publication_id',
        'case_key',
        'a_output',
        'b_output',
        'a_system',
        'b_system',
        'choice',
        'defects',
        'elapsed_ms',
        'recorded_at',
        'ip_tag',
        'exclusion',
      ])
      .expression((eb) =>
        eb
          .selectFrom('active')
          .innerJoin('outputs as a', (join) =>
            join
              .onRef('a.publication_id', '=', 'active.publication_id')
              .on('a.case_key', '=', caseKey)
              .on('a.system', '=', systems[0]),
          )
          .innerJoin('outputs as b', (join) =>
            join
              .onRef('b.publication_id', '=', 'active.publication_id')
              .on('b.case_key', '=', caseKey)
              .on('b.system', '=', systems[1]),
          )
          .select((sub) => [
            sub.val(crypto.randomUUID()).as('pair_id'),
            sub.val(actor).as('actor_id'),
            sub.val(family).as('family'),
            'active.publication_id',
            sub.val(caseKey).as('case_key'),
            'a.output_key as a_output',
            'b.output_key as b_output',
            'a.system as a_system',
            'b.system as b_system',
            sub.val('a' as const).as('choice'),
            sub.val('{"a": [], "b": []}').as('defects'),
            sub.val(0).as('elapsed_ms'),
            sub.val(options.recordedAt ?? Date.now()).as('recorded_at'),
            sub.val(options.ipTag ?? null).as('ip_tag'),
            sub.val(options.exclusion ?? null).as('exclusion'),
          ])
          .where('active.family', '=', family)
          .limit(1),
      ),
  ]);
}
