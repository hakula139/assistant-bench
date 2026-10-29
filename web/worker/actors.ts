import type { Context } from 'hono';
import { getSignedCookie, setSignedCookie } from 'hono/cookie';
import { z } from 'zod/mini';

import { ApiError, type Api, type Bindings } from './api';
import { ACTOR_MAX_AGE_MS, ACTORS_PER_TAG, WINDOW_MS } from './limits';

/** Sent as `__Host-bench-actor`, which Hono's `host` prefix adds. */
const COOKIE = 'bench-actor';
const SITEVERIFY = 'https://challenges.cloudflare.com/turnstile/v0/siteverify';
const TAG_BYTES = 16;

const siteverifyResult = z.object({ success: z.boolean(), hostname: z.optional(z.string()) });

/** HMAC-SHA-256 under `key`, imported once for every message the signer signs. */
export function signer(key: string): (message: string) => Promise<Uint8Array> {
  const encoder = new TextEncoder();
  const imported = crypto.subtle.importKey(
    'raw',
    encoder.encode(key),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  return async (message) =>
    new Uint8Array(await crypto.subtle.sign('HMAC', await imported, encoder.encode(message)));
}

export function hex(bytes: Uint8Array): string {
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

/** An IPv4 address as given, or an IPv6 address as its /64 prefix, since one host holds a /64. */
export function network(address: string): string {
  const halves = address.toLowerCase().split('::');
  if (halves.length === 1 && !address.includes(':')) {
    return address;
  }

  // An embedded IPv4 address fills the last two groups, which the prefix never reaches.
  const groups = (part: string | undefined) =>
    part === undefined || part === ''
      ? []
      : part.split(':').flatMap((group) => (group.includes('.') ? ['0', '0'] : [group]));
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

/** The opaque tag that stands for an address's network in every stored row. */
export async function ipTag(key: string, address: string): Promise<string> {
  return hex((await signer(key)(network(address))).slice(0, TAG_BYTES));
}

/** Checks a Turnstile token with Cloudflare, for the hostname this deployment's widget runs on. */
export async function verifyChallenge(
  env: Bindings,
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

/** Creates an actor unless its tag already admitted `ACTORS_PER_TAG` browsers in the window. */
export async function admit(db: D1Database, tag: string, now: number): Promise<string> {
  const id = crypto.randomUUID();
  const { meta } = await db
    .prepare(
      `INSERT INTO actors (id, created_at, ip_tag)
       SELECT ?1, ?2, ?3
       WHERE (SELECT count(*) FROM actors WHERE ip_tag = ?3 AND created_at > ?4) < ?5`,
    )
    .bind(id, now, tag, now - WINDOW_MS, ACTORS_PER_TAG)
    .run();
  if (meta.changes === 0) {
    throw new ApiError(429, 'rate_limited');
  }
  return id;
}

/** The actor ID the cookie carries under a valid signature, before any lookup. */
export async function claimedActor(c: Context<Api>): Promise<string | null> {
  return (await getSignedCookie(c, c.env.ACTOR_KEY, COOKIE, 'host')) || null;
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

/** Whether the claimed actor exists and its cookie has not outlived its lifetime. */
export async function knownActor(db: D1Database, actor: string | null, now: number) {
  if (actor === null) {
    return false;
  }
  const row = await db
    .prepare('SELECT 1 FROM actors WHERE id = ? AND created_at > ?')
    .bind(actor, now - ACTOR_MAX_AGE_MS)
    .first();
  return row !== null;
}

/** The request's actor, or 401 `admission_required`. */
export async function requireActor(c: Context<Api>, now: number): Promise<string> {
  const actor = c.get('claimed');
  if (actor === null || !(await knownActor(c.env.DB, actor, now))) {
    throw new ApiError(401, 'admission_required');
  }
  return actor;
}
