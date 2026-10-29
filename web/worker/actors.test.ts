import { env } from 'cloudflare:workers';
import { describe, expect, test } from 'vitest';

import { network } from './actors';
import { ACTOR_MAX_AGE_MS } from './limits';
import { actorOf, admitted, browser, call, stubSiteverify, WRITING } from './test/support';

describe('network', () => {
  test.each([
    ['an IPv4 address', '192.0.2.1', '192.0.2.1'],
    ['a full IPv6 address', '2001:0db8:0001:0002:0003:0004:0005:0006', '2001:db8:1:2::/64'],
    ['a compressed IPv6 address', '2001:db8::1', '2001:db8:0:0::/64'],
    ['a compressed prefix', '2001:db8:1:2:3::', '2001:db8:1:2::/64'],
    ['the unspecified address', '::', '0:0:0:0::/64'],
    ['an embedded IPv4 address', '2001:db8:1:2::192.0.2.1', '2001:db8:1:2::/64'],
    ['upper-case digits', '2001:DB8:A::1', '2001:db8:a:0::/64'],
  ])('maps %s to its network', (_, address, expected) => {
    expect(network(address)).toBe(expected);
  });
});

describe('POST /api/actors', () => {
  test('admits a verified browser with a host-only cookie and a keyed tag', async () => {
    const from = browser('192.0.2.7');
    const verify = stubSiteverify({ success: true, hostname: env.TURNSTILE_HOSTNAME });

    const response = await call(from, 'POST', '/actors', { token: 'token' });

    expect(response.status).toBe(204);
    const cookie = response.headers.get('Set-Cookie') ?? '';
    expect(cookie).toMatch(/^__Host-bench-actor=/);
    expect(cookie).toContain('Max-Age=34560000');
    expect(cookie).toContain('Path=/');
    expect(cookie).toContain('HttpOnly');
    expect(cookie).toContain('Secure');
    expect(cookie).toContain('SameSite=Strict');
    const [, init] = verify.mock.calls[0];
    expect(Object.fromEntries(init?.body as URLSearchParams)).toEqual({
      secret: env.TURNSTILE_SECRET,
      response: 'token',
      remoteip: '192.0.2.7',
    });
    const actor = await env.DB.prepare('SELECT ip_tag FROM actors WHERE id = ?')
      .bind(await actorOf(from))
      .first<{ ip_tag: string }>();
    expect(actor?.ip_tag).toMatch(/^[0-9a-f]{32}$/);
  });

  test.each([
    ['a failed challenge', { success: false, hostname: env.TURNSTILE_HOSTNAME }],
    ['another hostname', { success: true, hostname: 'bench.hakula.xyz' }],
  ])('refuses %s', async (_, result) => {
    stubSiteverify(result);

    const response = await call(browser(), 'POST', '/actors', { token: 'token' });

    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ error: 'challenge_failed' });
    expect(await env.DB.prepare('SELECT count(*) AS n FROM actors').first('n')).toBe(0);
  });

  test('admits five browsers per network a day, counting an IPv6 /64 as one network', async () => {
    for (let index = 1; index <= 5; index++) {
      await admitted(`2001:db8:1:2::${index}`);
    }
    stubSiteverify({ success: true, hostname: env.TURNSTILE_HOSTNAME });

    const sixth = await call(browser('2001:db8:1:2::6'), 'POST', '/actors', { token: 'token' });
    const elsewhere = await call(browser('2001:db8:1:3::1'), 'POST', '/actors', {
      token: 'token',
    });

    expect(sixth.status).toBe(429);
    expect(await sixth.json()).toEqual({ error: 'rate_limited' });
    expect(elsewhere.status).toBe(204);
  });

  test('keeps the actor a valid cookie already names', async () => {
    const from = await admitted();
    const verify = stubSiteverify({ success: true, hostname: env.TURNSTILE_HOSTNAME });

    const response = await call(from, 'POST', '/actors', { token: 'token' });

    expect(response.status).toBe(204);
    expect(response.headers.get('Set-Cookie')).toBeNull();
    expect(verify).not.toHaveBeenCalled();
    expect(await env.DB.prepare('SELECT count(*) AS n FROM actors').first('n')).toBe(1);
  });
});

describe('admission', () => {
  test.each([
    ['no cookie', () => Promise.resolve(browser())],
    [
      'an unsigned cookie',
      async () => {
        const from = await admitted();
        from.cookie = `__Host-bench-actor=${await actorOf(from)}`;
        return from;
      },
    ],
    [
      'a tampered signature',
      async () => {
        const from = await admitted();
        from.cookie = `${from.cookie ?? ''}x`;
        return from;
      },
    ],
    [
      'an unknown actor',
      async () => {
        const from = await admitted();
        await env.DB.prepare('DELETE FROM actors').run();
        return from;
      },
    ],
    [
      'an expired actor',
      async () => {
        const from = await admitted();
        await env.DB.prepare('UPDATE actors SET created_at = ?')
          .bind(Date.now() - ACTOR_MAX_AGE_MS - 1)
          .run();
        return from;
      },
    ],
  ])('answers %s with the sitekey', async (_, arrange) => {
    const from = await arrange();

    const response = await call(from, 'GET', `/pair?family=${WRITING}`);

    expect(response.status).toBe(401);
    expect(await response.json()).toEqual({
      error: 'admission_required',
      sitekey: env.TURNSTILE_SITEKEY,
    });
  });
});
