import { env, exports } from 'cloudflare:workers';
import { vi } from 'vitest';

import type { PairState } from '@/lib/arena';

export const ORIGIN = 'http://localhost:8790';
export const WRITING = 'writing-fixture';
export const CODING = 'coding-fixture';

/** One browser: the address it sends from and the cookie it holds. */
export interface Browser {
  ip: string;
  cookie: string | null;
}

export const browser = (ip = '192.0.2.1'): Browser => ({ ip, cookie: null });

/** Sends a request as the browser would, keeping any cookie the Worker sets. */
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

/** Answers Turnstile's siteverify with `result`, and records each verification request. */
export function stubSiteverify(result: { success: boolean; hostname?: string }) {
  const original = globalThis.fetch;
  return vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
    const url = input instanceof Request ? input.url : String(input);
    return url === 'https://challenges.cloudflare.com/turnstile/v0/siteverify'
      ? Promise.resolve(Response.json(result))
      : original(input, init);
  });
}

/** A browser that passed admission. */
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
  const row = await env.DB.prepare('SELECT id FROM actors ORDER BY created_at DESC')
    .all<{ id: string }>()
    .then(({ results }) => results.find(({ id }) => from.cookie?.includes(id)));
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

/** The pending pair's case and its systems as A and B, read from the store. */
export async function pendingRow(pair: string) {
  return env.DB.prepare(
    `SELECT pairs.case_key, a.system AS a_system, b.system AS b_system, pairs.issued_at
     FROM pairs
     JOIN outputs AS a ON a.publication_id = pairs.publication_id AND a.output_key = pairs.a_output
     JOIN outputs AS b ON b.publication_id = pairs.publication_id AND b.output_key = pairs.b_output
     WHERE pairs.id = ?`,
  )
    .bind(pair)
    .first<{ case_key: string; a_system: string; b_system: string; issued_at: number }>();
}

export async function vote(from: Browser, pair: string, choice = 'a') {
  return call(from, 'POST', '/votes', { pair, choice, defects: { a: [], b: [] } });
}

/**
 * Publishes and activates a text family whose outputs `cases` lists per case and system, with
 * null for a failing output. Equal strings are identical content.
 */
export async function publish(
  family: string,
  cases: Record<string, Record<string, (string | null)[]>>,
  id = `${family}-publication`,
): Promise<void> {
  const statements = [
    env.DB.prepare(
      `INSERT INTO publications (id, family, display_name, aspect, render, defects, reference,
         systems, case_count, output_count, created_at)
       VALUES (?, ?, 'Synthetic', 'writing', 'text', '["facts", "tone"]', 'acme-large', '{}', 0, 0, 0)`,
    ).bind(id, family),
  ];
  for (const [caseKey, systems] of Object.entries(cases)) {
    statements.push(
      env.DB.prepare(
        `INSERT INTO cases (publication_id, case_key, category, instruction, checklist)
         VALUES (?, ?, 'reply', ?, '[]')`,
      ).bind(id, caseKey, `Instruction ${caseKey}`),
    );
    for (const [system, contents] of Object.entries(systems)) {
      contents.forEach((content, index) => {
        statements.push(
          env.DB.prepare(
            `INSERT INTO outputs (publication_id, output_key, case_key, system, fingerprint,
               verdict, errored, length, content_digest, content)
             VALUES (?, ?, ?, ?, ?, ?, 0, 1, ?, ?)`,
          ).bind(
            id,
            `${caseKey}/${system}/${index}`,
            caseKey,
            system,
            `fp-${system}`,
            content === null ? 'fail' : 'pass',
            content ?? `failed ${caseKey}/${system}/${index}`,
            content,
          ),
        );
      });
    }
  }
  statements.push(
    env.DB.prepare(
      `INSERT INTO active (family, publication_id, activated_at) VALUES (?, ?, 0)
       ON CONFLICT (family) DO UPDATE SET publication_id = excluded.publication_id`,
    ).bind(family, id),
  );
  await env.DB.batch(statements);
}

/** Records a counted vote from a separate actor, for coverage. */
export async function seedVote(
  family: string,
  caseKey: string,
  systems: readonly [string, string],
  options: { actor?: string; ipTag?: string; recordedAt?: number; exclusion?: string } = {},
) {
  const actor = options.actor ?? crypto.randomUUID();
  await env.DB.batch([
    env.DB.prepare(
      'INSERT OR IGNORE INTO actors (id, created_at, ip_tag) VALUES (?, 0, NULL)',
    ).bind(actor),
    env.DB.prepare(
      `INSERT INTO votes (pair_id, actor_id, family, publication_id, case_key, a_output, b_output,
         a_system, b_system, choice, defects, elapsed_ms, recorded_at, ip_tag, exclusion)
       SELECT ?, ?, ?, active.publication_id, ?, a.output_key, b.output_key, a.system, b.system,
         'a', '{"a": [], "b": []}', 0, ?, ?, ?
       FROM active
       JOIN outputs AS a ON a.publication_id = active.publication_id AND a.case_key = ?
         AND a.system = ?
       JOIN outputs AS b ON b.publication_id = active.publication_id AND b.case_key = ?
         AND b.system = ?
       WHERE active.family = ? LIMIT 1`,
    ).bind(
      crypto.randomUUID(),
      actor,
      family,
      caseKey,
      options.recordedAt ?? Date.now(),
      options.ipTag ?? null,
      options.exclusion ?? null,
      caseKey,
      systems[0],
      caseKey,
      systems[1],
      family,
    ),
  ]);
}
