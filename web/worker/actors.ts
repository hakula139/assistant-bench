import type { Context } from 'hono';
import { getSignedCookie, setSignedCookie } from 'hono/cookie';
import * as z from 'zod/mini';

import { ApiError, type Api, type PublicBindings } from './api';
import { first, queries, run } from './db';
import { hex, signer } from './hmac';
import { ACTOR_MAX_AGE_MS, ACTORS_PER_TAG, WINDOW_MS } from './limits';

/** Sent as `__Host-bench-actor`, which Hono's `host` prefix adds. */
const COOKIE = 'bench-actor';
const SITEVERIFY = 'https://challenges.cloudflare.com/turnstile/v0/siteverify';
const TAG_BYTES = 16;

const siteverifyResult = z.object({ success: z.boolean(), hostname: z.optional(z.string()) });

export function network(address: string): string {
  const halves = address.toLowerCase().split('::');
  if (halves.length === 1 && !address.includes(':')) {
    return address;
  }

  const groups = (part: string | undefined) =>
    part === undefined || part === '' ? [] : part.split(':');
  const head = groups(halves[0]);
  const tail = groups(halves[1]);
  const full =
    halves.length === 1
      ? head
      : [...head, ...Array<string>(8 - head.length - tail.length).fill('0'), ...tail];
  return `${full
    .slice(0, 4)
    .map((group) => Number.parseInt(group, 16).toString(16))
    .join(':')}::/64`;
}

export async function ipTag(key: string, address: string): Promise<string> {
  return hex((await signer(key)(network(address))).slice(0, TAG_BYTES));
}

export async function verifyChallenge(
  env: PublicBindings,
  token: string,
  address: string,
): Promise<void> {
  const response = await fetch(SITEVERIFY, {
    method: 'POST',
    body: new URLSearchParams({ secret: env.TURNSTILE_SECRET, response: token, remoteip: address }),
  });
  const result = siteverifyResult.parse(await response.json());
  if (!result.success || result.hostname !== env.TURNSTILE_HOSTNAME) {
    throw new ApiError(400, 'challenge_failed');
  }
}

export async function admit(db: D1Database, tag: string, now: number): Promise<string> {
  const id = crypto.randomUUID();
  const { changes } = await run(
    db,
    queries
      .insertInto('actors')
      .columns(['id', 'created_at', 'ip_tag'])
      .expression(
        queries
          .selectNoFrom((eb) => [
            eb.val(id).as('id'),
            eb.val(now).as('created_at'),
            eb.val(tag).as('ip_tag'),
          ])
          .where((eb) =>
            eb(
              eb
                .selectFrom('actors')
                .select((sub) => sub.fn.countAll<number>().as('count'))
                .where('ip_tag', '=', tag)
                .where('created_at', '>', now - WINDOW_MS),
              '<',
              ACTORS_PER_TAG,
            ),
          ),
      ),
  );
  if (changes === 0) {
    throw new ApiError(429, 'daily_limit');
  }
  return id;
}

export async function admitOwner(db: D1Database, actor: string, now: number): Promise<void> {
  await run(
    db,
    queries
      .insertInto('actors')
      .values({ id: actor, created_at: now, ip_tag: null })
      .onConflict((oc) => oc.doNothing()),
  );
}

export async function claimedActor(c: Context<Api>): Promise<string | null> {
  const actor = await getSignedCookie(c, c.env.ACTOR_KEY, COOKIE, 'host');
  return typeof actor === 'string' && actor !== '' ? actor : null;
}

export async function issueCookie(c: Context<Api>, actor: string): Promise<void> {
  await setSignedCookie(c, COOKIE, actor, c.env.ACTOR_KEY, {
    prefix: 'host',
    httpOnly: true,
    secure: true,
    sameSite: 'Strict',
    path: '/',
    maxAge: ACTOR_MAX_AGE_MS / 1000,
  });
}

export async function knownActor(
  db: D1Database,
  actor: string | null,
  now: number,
): Promise<boolean> {
  if (actor === null) {
    return false;
  }

  const row = await first(
    db,
    queries
      .selectFrom('actors')
      .select('id')
      .where('id', '=', actor)
      .where('created_at', '>', now - ACTOR_MAX_AGE_MS),
  );
  return row !== null;
}

/** The actor, or 401 `reload_required` in private mode and `admission_required` otherwise. */
export async function requireActor(c: Context<Api>, now: number): Promise<string> {
  const actor = c.get('claimed');
  if (c.env.STORE === 'private') {
    if (
      actor !== c.env.PRIVATE_ACTOR_ID ||
      (await first(c.env.DB, queries.selectFrom('actors').select('id').where('id', '=', actor))) ===
        null
    ) {
      throw new ApiError(401, 'reload_required');
    }
    return actor;
  }

  if (actor === null || !(await knownActor(c.env.DB, actor, now))) {
    throw new ApiError(401, 'admission_required');
  }
  return actor;
}
