import { env } from 'cloudflare:workers';
import { Hono } from 'hono';
import { setSignedCookie } from 'hono/cookie';
import { exportJWK, generateKeyPair, SignJWT, UnsecuredJWT, type JWK } from 'jose';
import { beforeAll, describe, expect, test, vi } from 'vitest';

import type { PairState } from '@/lib/arena';

import { verifyOwner } from './access';
import { app } from './index';
import { ACTOR_MAX_AGE_MS } from './limits';
import { publish, WRITING } from './test/support';

const ORIGIN = 'https://private.example.com' as Cloudflare.PrivateEnv['APP_ORIGIN'];
const OWNER = '00000000-0000-4000-8000-000000000001';
const EMAILS = ['first@example.com', 'second@example.com'];
let primary: Awaited<ReturnType<typeof generateKeyPair>>;
let rotated: Awaited<ReturnType<typeof generateKeyPair>>;
let primaryJwk: JWK;
let rotatedJwk: JWK;

beforeAll(async () => {
  [primary, rotated] = await Promise.all([generateKeyPair('RS256'), generateKeyPair('RS256')]);
  primaryJwk = { ...(await exportJWK(primary.publicKey)), kid: 'primary', alg: 'RS256' };
  rotatedJwk = { ...(await exportJWK(rotated.publicKey)), kid: 'rotated', alg: 'RS256' };
});

function deployment() {
  const assets = vi.fn(() =>
    Promise.resolve(
      new Response('<!doctype html><title>Private fixture</title>', {
        headers: {
          'Content-Type': 'text/html',
          'Cache-Control': 'public, max-age=3600',
          'Content-Security-Policy': "default-src 'self'; frame-ancestors 'none'",
        },
      }),
    ),
  );
  const settings: Cloudflare.PrivateEnv = {
    DB: env.DB,
    RATE_LIMIT: { limit: () => Promise.resolve({ success: true }) },
    ASSETS: {
      fetch: assets,
      connect: () => {
        throw new Error('Unexpected asset connection.');
      },
    },
    APP_ORIGIN: ORIGIN,
    STORE: 'private',
    ACTOR_KEY: 'test-private-actor-key',
    IP_TAG_KEY: 'test-private-ip-key',
    ACCESS_ISSUER: `https://access-${crypto.randomUUID()}.example.com`,
    ACCESS_AUDIENCE: 'private-fixture-audience',
    PRIVATE_OWNER_EMAILS: JSON.stringify(EMAILS),
    PRIVATE_ACTOR_ID: OWNER,
  };
  const jwks = { keys: [primaryJwk] };
  const fetched = vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
    const url = input instanceof Request ? input.url : String(input);
    expect(url).toBe(`${settings.ACCESS_ISSUER}/cdn-cgi/access/certs`);
    return Promise.resolve(Response.json(jwks));
  });
  const claims = (overrides: Record<string, unknown> = {}) => ({
    iss: settings.ACCESS_ISSUER,
    aud: settings.ACCESS_AUDIENCE,
    sub: 'synthetic-owner',
    email: EMAILS[0],
    exp: Math.floor(Date.now() / 1000) + 3600,
    ...overrides,
  });
  const token = (overrides: Record<string, unknown> = {}, rotate = false) =>
    new SignJWT(claims(overrides))
      .setProtectedHeader({ alg: 'RS256', kid: rotate ? 'rotated' : 'primary' })
      .sign(rotate ? rotated.privateKey : primary.privateKey);
  const request = (assertion: string, headers: Record<string, string> = {}) =>
    new Request(ORIGIN, { headers: { 'Cf-Access-Jwt-Assertion': assertion, ...headers } });
  const send = (
    path: string,
    assertion: string | null,
    options: {
      cookie?: string | null;
      method?: 'GET' | 'HEAD' | 'POST';
      origin?: string | null;
      body?: unknown;
      headers?: Record<string, string>;
    } = {},
  ) => {
    const method = options.method ?? 'GET';
    const headers = new Headers({ 'CF-Connecting-IP': '192.0.2.10', ...options.headers });
    if (assertion !== null) {
      headers.set('Cf-Access-Jwt-Assertion', assertion);
    }
    if (options.cookie) {
      headers.set('Cookie', options.cookie);
    }
    if (method === 'POST') {
      headers.set('Content-Type', 'application/json');
      if (options.origin !== null) {
        headers.set('Origin', options.origin ?? ORIGIN);
      }
    }
    return app.fetch(
      new Request(new URL(path, ORIGIN), {
        method,
        headers,
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
      }),
      settings,
    );
  };
  return { settings, assets, jwks, fetched, claims, token, request, send };
}

function expectPrivate(response: Response, preview = false) {
  expect(response.headers.get('Cache-Control')).toBe('no-store');
  expect(response.headers.get('X-Frame-Options')).toBe('SAMEORIGIN');
  const policy = response.headers
    .get('Content-Security-Policy')
    ?.split(';')
    .map((s) => s.trim());
  expect(policy?.filter((directive) => directive.startsWith('frame-ancestors'))).toEqual([
    "frame-ancestors 'self'",
  ]);
  if (preview) {
    expect(policy).toEqual(
      expect.arrayContaining([
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
      ]),
    );
    expect(response.headers.get('Referrer-Policy')).toBe('no-referrer');
    expect(response.headers.get('X-Content-Type-Options')).toBe('nosniff');
    expect(response.headers.get('X-DNS-Prefetch-Control')).toBe('off');
  }
}

async function cookieFor(actor: string) {
  const signer = new Hono().get('/', async (c) => {
    await setSignedCookie(c, 'bench-actor', actor, 'test-private-actor-key', {
      prefix: 'host',
      path: '/',
      secure: true,
      httpOnly: true,
      sameSite: 'Strict',
    });
    return c.text('Signed synthetic actor.');
  });
  return (await signer.request(ORIGIN)).headers.get('Set-Cookie')?.split(';')[0] ?? '';
}

describe('verifyOwner', () => {
  test.each(EMAILS)(
    'verifies the real signature for owner %s and an audience array',
    async (email) => {
      const fixture = deployment();
      const token = await fixture.token({
        email,
        aud: ['unrelated', fixture.settings.ACCESS_AUDIENCE],
      });

      const verified = await verifyOwner(fixture.request(token), fixture.settings);

      expect(verified).toBe(true);
      expect(fixture.fetched).toHaveBeenCalledOnce();
    },
  );

  test.each([
    ['a foreign issuer', { iss: 'https://other.example.com' }],
    ['a foreign audience', { aud: 'private-fixture-audience-suffix' }],
    ['a missing expiration', { exp: undefined }],
    ['an expired assertion', { exp: 1 }],
    ['an assertion not yet valid', { nbf: Math.floor(Date.now() / 1000) + 3600 }],
    ['a missing subject', { sub: undefined }],
    ['an empty subject', { sub: '' }],
    ['a non-string subject', { sub: 12 }],
    ['a missing email', { email: undefined }],
    ['a non-string email', { email: ['first@example.com'] }],
    ['another email', { email: 'outsider@example.com' }],
    ['a differently cased email', { email: 'FIRST@example.com' }],
  ])('rejects %s even when correctly signed', async (_, claims) => {
    const fixture = deployment();
    const token = await fixture.token(claims);

    expect(await verifyOwner(fixture.request(token), fixture.settings)).toBe(false);
  });

  test.each(['unsigned', 'wrong signature', 'symmetric algorithm', 'Bearer prefix'])(
    'rejects %s without trusting JWT metadata',
    async (scenario) => {
      const fixture = deployment();
      const claims = fixture.claims();
      const token =
        scenario === 'unsigned'
          ? new UnsecuredJWT(claims).encode()
          : scenario === 'wrong signature'
            ? await new SignJWT(claims)
                .setProtectedHeader({
                  alg: 'RS256',
                  kid: 'primary',
                  jku: 'https://other.example.com/keys',
                })
                .sign(rotated.privateKey)
            : scenario === 'symmetric algorithm'
              ? await new SignJWT(claims)
                  .setProtectedHeader({ alg: 'HS256', kid: 'primary' })
                  .sign(new TextEncoder().encode('synthetic-symmetric-signing-key'))
              : `Bearer ${await fixture.token()}`;

      expect(await verifyOwner(fixture.request(token), fixture.settings)).toBe(false);
    },
  );

  test.each(['Cf-Access-Authenticated-User-Email', 'Authorization', 'Cookie'])(
    'ignores %s as an alternative to the raw assertion header',
    async (header) => {
      const fixture = deployment();
      const token = await fixture.token();
      const value =
        header === 'Cookie'
          ? `CF_Authorization=${token}`
          : header === 'Authorization'
            ? `Bearer ${token}`
            : EMAILS[0];

      const verified = await verifyOwner(
        new Request(ORIGIN, { headers: { [header]: value } }),
        fixture.settings,
      );

      expect(verified).toBe(false);
      expect(fixture.fetched).not.toHaveBeenCalled();
    },
  );

  test('rejects another RSA algorithm even when its public key is in the trusted key set', async () => {
    const fixture = deployment();
    const alternate = await generateKeyPair('RS512');
    fixture.jwks.keys[0] = {
      ...(await exportJWK(alternate.publicKey)),
      kid: 'alternate',
      alg: 'RS512',
    };
    const token = await new SignJWT(fixture.claims())
      .setProtectedHeader({ alg: 'RS512', kid: 'alternate' })
      .sign(alternate.privateKey);

    expect(await verifyOwner(fixture.request(token), fixture.settings)).toBe(false);
  });

  test.each([
    'not JSON',
    '["first@example.com"]',
    '["first@example.com","second@example.com","third@example.com"]',
    '["first@example.com","invalid"]',
  ])('fails closed for invalid owner configuration %s', async (owners) => {
    const fixture = deployment();
    fixture.settings.PRIVATE_OWNER_EMAILS = owners;

    expect(await verifyOwner(fixture.request(await fixture.token()), fixture.settings)).toBe(false);
  });

  test.each(['unavailable', 'malformed', 'redirect'])(
    'fails closed when the trusted key endpoint is %s',
    async (scenario) => {
      const fixture = deployment();
      fixture.fetched.mockResolvedValueOnce(
        scenario === 'unavailable'
          ? new Response(null, { status: 503 })
          : scenario === 'redirect'
            ? Response.redirect('https://other.example.com/keys')
            : Response.json({ keys: 'invalid' }),
      );

      expect(await verifyOwner(fixture.request(await fixture.token()), fixture.settings)).toBe(
        false,
      );
    },
  );

  test('caches trusted keys, refreshes a rotated key after cooldown, and expires retired keys', async () => {
    const fixture = deployment();
    const first = fixture.request(await fixture.token());
    const second = fixture.request(await fixture.token({}, true));
    const now = Date.now();
    expect(await verifyOwner(first, fixture.settings)).toBe(true);
    expect(await verifyOwner(first, fixture.settings)).toBe(true);
    expect(fixture.fetched).toHaveBeenCalledOnce();

    fixture.jwks.keys.push(rotatedJwk);
    expect(await verifyOwner(second, fixture.settings)).toBe(false);
    const clock = vi.spyOn(Date, 'now').mockReturnValue(now + 31_000);
    expect(await verifyOwner(second, fixture.settings)).toBe(true);
    expect(fixture.fetched).toHaveBeenCalledTimes(2);

    fixture.jwks.keys.splice(0, 1);
    clock.mockReturnValue(now + 11 * 60_000);
    expect(await verifyOwner(first, fixture.settings)).toBe(false);
    expect(await verifyOwner(second, fixture.settings)).toBe(true);
    expect(fixture.fetched).toHaveBeenCalledTimes(3);
  });
});

describe('private request admission', () => {
  test.each([
    '/',
    '/vote/',
    '/_astro/app.js',
    '/api/families',
    `/api/previews/${OWNER}/a`,
    '/api/missing',
  ])(
    'refuses %s before assets or database access when only a forged email is supplied',
    async (path) => {
      const fixture = deployment();
      const prepare = vi.spyOn(env.DB, 'prepare');

      const response = await fixture.send(path, null, {
        headers: { 'Cf-Access-Authenticated-User-Email': EMAILS[0] },
      });

      expect(response.status).toBe(403);
      expectPrivate(response, path.startsWith('/api/previews/'));
      expect(fixture.assets).not.toHaveBeenCalled();
      expect(prepare).not.toHaveBeenCalled();
    },
  );

  test('refuses a different deployment origin even with a valid assertion', async () => {
    const fixture = deployment();

    const response = await fixture.send('https://other.example.com/vote/', await fixture.token());

    expect(response.status).toBe(403);
    expectPrivate(response);
    expect(fixture.assets).not.toHaveBeenCalled();
    expect(fixture.fetched).not.toHaveBeenCalled();
  });

  test.each([
    ['a script asset', '/_astro/app.js', 'GET', 200, 'application/javascript'],
    ['a missing page', '/vote/', 'GET', 404, 'text/html'],
    ['a non-HTML vote response', '/vote/', 'GET', 200, 'text/plain'],
    ['a HEAD vote request', '/vote/', 'HEAD', 200, 'text/html'],
    ['a vote redirect', '/vote', 'GET', 307, 'text/html'],
  ] as const)(
    'serves %s without creating an actor or cookie',
    async (_, path, method, status, type) => {
      const fixture = deployment();
      fixture.assets.mockResolvedValueOnce(
        new Response('Fixture', { status, headers: { 'Content-Type': type } }),
      );

      const response = await fixture.send(path, await fixture.token(), { method });

      expect(response.status).toBe(status);
      expectPrivate(response);
      expect(response.headers.get('Set-Cookie')).toBeNull();
      expect(await env.DB.prepare('SELECT count(*) FROM actors').first('count(*)')).toBe(0);
    },
  );

  test.each(['missing', 'unsigned', 'tampered', 'another actor', 'absent from database'])(
    'requires a page reload for a %s actor cookie, even with valid Access',
    async (scenario) => {
      const fixture = deployment();
      const admitted = await fixture.send('/vote/', await fixture.token());
      const valid = admitted.headers.get('Set-Cookie')?.split(';')[0];
      let cookie = valid;
      if (scenario === 'missing') {
        cookie = undefined;
      } else if (scenario === 'unsigned') {
        cookie = `__Host-bench-actor=${OWNER}`;
      } else if (scenario === 'tampered') {
        cookie = `${valid ?? ''}x`;
      } else if (scenario === 'another actor') {
        cookie = await cookieFor('00000000-0000-4000-8000-000000000002');
      } else {
        await env.DB.prepare('DELETE FROM actors').run();
      }

      const response = await fixture.send(`/api/pair?family=${WRITING}`, await fixture.token(), {
        cookie,
      });

      expect(response.status).toBe(401);
      expectPrivate(response);
      expect(await response.json()).toEqual({ error: 'reload_required' });
    },
  );

  test.each([
    ['/api/families', 'GET'],
    ['/api/votes', 'POST'],
  ] as const)(
    'requires the actor cookie for %s even with a valid assertion',
    async (path, method) => {
      const fixture = deployment();

      const response = await fixture.send(path, await fixture.token(), {
        method,
        body:
          method === 'POST' ? { pair: OWNER, choice: 'a', defects: { a: [], b: [] } } : undefined,
      });

      expect(response.status).toBe(401);
      expectPrivate(response);
      expect(await response.json()).toEqual({ error: 'reload_required' });
    },
  );

  test.each([null, 'https://other.example.com'])(
    'keeps the POST Origin guard with origin %s and a valid owner cookie',
    async (origin) => {
      const fixture = deployment();
      const admitted = await fixture.send('/vote/', await fixture.token());

      const response = await fixture.send('/api/votes/undo', await fixture.token(), {
        method: 'POST',
        cookie: admitted.headers.get('Set-Cookie')?.split(';')[0],
        origin,
        body: { family: WRITING },
      });

      expect(response.status).toBe(403);
      expectPrivate(response);
      expect(await response.json()).toEqual({ error: 'forbidden' });
    },
  );

  test('recovers one permanent actor, pending pair and undo from both owner emails on fresh devices', async () => {
    const fixture = deployment();
    const created = Date.now() - 2 * ACTOR_MAX_AGE_MS;
    await env.DB.prepare('INSERT INTO actors (id, created_at) VALUES (?, ?)')
      .bind(OWNER, created)
      .run();
    const assertions = await Promise.all(EMAILS.map((email) => fixture.token({ email })));
    const cookies: string[] = [];
    const pairs: PairState[] = [];

    for (const assertion of assertions) {
      const admitted = await fixture.send('/vote/', assertion);
      expect(admitted.status).toBe(200);
      expectPrivate(admitted);
      expect(admitted.headers.get('Content-Security-Policy')).toContain("default-src 'self'");
      const set = admitted.headers.get('Set-Cookie') ?? '';
      expect(set).toMatch(/^__Host-bench-actor=/);
      for (const attribute of ['Secure', 'HttpOnly', 'Path=/', 'SameSite=Strict']) {
        expect(set).toContain(attribute);
      }
      expect(set).not.toContain('Domain=');
      cookies.push(set.split(';')[0]);
      const response = await fixture.send(`/api/pair?family=${WRITING}`, assertion, {
        cookie: cookies.at(-1),
      });
      expect(response.status).toBe(200);
      pairs.push(await response.json<PairState>());
    }
    expect(pairs[0].pair?.id).toBeTruthy();
    expect(pairs[1]).toEqual(pairs[0]);
    expect(await env.DB.prepare('SELECT id, created_at, ip_tag FROM actors').all()).toMatchObject({
      results: [{ id: OWNER, created_at: created, ip_tag: null }],
    });
    const admission = await fixture.send('/api/actors', assertions[0], {
      method: 'POST',
      cookie: cookies[0],
      body: { token: 'unused-private-challenge' },
    });
    expect(admission.status).toBe(204);
    const vote = await fixture.send('/api/votes', assertions[1], {
      method: 'POST',
      cookie: cookies[1],
      body: { pair: pairs[0].pair?.id, choice: 'a', defects: { a: [], b: [] } },
    });
    expect(vote.status).toBe(204);
    const progress = await fixture.send(`/api/pair?family=${WRITING}`, assertions[0], {
      cookie: cookies[0],
    });
    expect((await progress.json<PairState>()).votes).toBe(1);
    const undo = await fixture.send('/api/votes/undo', assertions[0], {
      method: 'POST',
      cookie: cookies[0],
      body: { family: WRITING },
    });
    expect(undo.status).toBe(204);
    const restored = await fixture.send(`/api/pair?family=${WRITING}`, assertions[1], {
      cookie: cookies[1],
    });
    expect((await restored.json<PairState>()).pair?.id).toBe(pairs[0].pair?.id);
  });

  test('preserves isolated HTML previews through private access success and refusals', async () => {
    const fixture = deployment();
    const document = (text: string) =>
      `<!doctype html><html><head><title>Fixture</title></head><body><h1>${text}</h1></body></html>`;
    await publish(
      'html-fixture',
      { first: { 'acme-large': [document('First')], 'acme-small': [document('Second')] } },
      undefined,
      'html',
    );
    const assertion = await fixture.token();
    const admitted = await fixture.send('/vote/', assertion);
    const cookie = admitted.headers.get('Set-Cookie')?.split(';')[0];
    const pair = await (
      await fixture.send('/api/pair?family=html-fixture', assertion, { cookie })
    ).json<PairState>();
    const path = `/api/previews/${pair.pair?.id}/a`;

    const success = await fixture.send(path, assertion, { cookie });
    const missingCookie = await fixture.send(path, assertion);
    const missingAccess = await fixture.send(path, null, { cookie });
    const notFound = await fixture.send(`/api/previews/${crypto.randomUUID()}/a`, assertion, {
      cookie,
    });

    expect(success.status).toBe(200);
    expect(await success.text()).toContain('<h1>');
    expect(missingCookie.status).toBe(401);
    expect(missingAccess.status).toBe(403);
    expect(notFound.status).toBe(404);
    for (const response of [success, missingCookie, missingAccess, notFound]) {
      expectPrivate(response, true);
    }
  });
});
