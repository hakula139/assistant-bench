import { env } from 'cloudflare:workers';
import { describe, expect, test, vi } from 'vitest';

import { admitted, browser, call, pairOf, publish, vote, type Browser } from './test/support';

const HTML = 'frontend-fixture';
const unavailable = 'Preview unavailable. Reload the voting page to try again.';
const document = (label: string) =>
  `<!doctype html>\r\n<html><head><title>${label}</title></head><body><h1>${label}</h1></body></html>\r\n`;

async function publishHtml(id = 'html-publication', prefix = '') {
  await publish(
    HTML,
    {
      first: {
        'acme-large': [document(`${prefix}First A`)],
        'acme-small': [document(`${prefix}First B`)],
      },
      second: {
        'acme-large': [document(`${prefix}Second A`)],
        'acme-small': [document(`${prefix}Second B`)],
      },
    },
    id,
    'html',
  );
}

async function htmlPair(from: Browser) {
  const { pair } = await pairOf(from, HTML);
  expect(pair?.render).toBe('html');
  if (pair?.render !== 'html') {
    throw new Error('Expected an HTML pair.');
  }
  return pair;
}

function expectIsolation(response: Response) {
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
    ].sort(),
  );
  expect(response.headers.get('X-Frame-Options')).toBe('SAMEORIGIN');
  expect(response.headers.get('X-DNS-Prefetch-Control')).toBe('off');
  expect(response.headers.get('Referrer-Policy')).toBe('no-referrer');
  expect(response.headers.get('X-Content-Type-Options')).toBe('nosniff');
}

describe('GET /api/previews/:pair/:side', () => {
  test('serves only the selected unchanged HTML and omits both bodies from pair JSON', async () => {
    await publishHtml();
    const from = await admitted();
    const pair = await htmlPair(from);
    const row = await env.DB.prepare('SELECT a_output, b_output FROM pairs WHERE id = ?')
      .bind(pair.id)
      .first<{ a_output: string; b_output: string }>();
    expect(row).not.toBeNull();
    if (row === null) {
      throw new Error('Expected a stored pair.');
    }
    const contents = [];

    for (const side of ['a', 'b'] as const) {
      const expected = await env.DB.prepare('SELECT content FROM outputs WHERE output_key = ?')
        .bind(row[`${side}_output`])
        .first<string>('content');

      const response = await call(from, 'GET', `/previews/${pair.id}/${side}`);

      expect(response.status).toBe(200);
      expect(response.headers.get('Content-Type')).toContain('text/html');
      expectIsolation(response);
      const content = await response.text();
      expect(content).toBe(expected);
      expect(content).toContain('\r\n');
      contents.push(content);
    }

    expect(contents[0]).not.toBe(contents[1]);
    expect(pair).not.toHaveProperty('a');
    expect(pair).not.toHaveProperty('b');
    expect(JSON.stringify(pair)).not.toContain('acme');
    expect(JSON.stringify(pair)).not.toContain('<html>');
  });

  test.each([
    ['an invalid UUID', 'invalid-id', 400],
    ['an invalid side', 'side', 400],
    ['an unknown pair', 'unknown', 404],
    ['a different actor', 'actor', 404],
    ['a text publication', 'render', 404],
    ['an inactive publication', 'publication', 404],
    ['no actor cookie', 'cookie', 401],
  ] as const)(
    'rejects %s with the same safe body and isolation headers',
    async (_, scenario, code) => {
      await publishHtml();
      let from = await admitted();
      const pair = await htmlPair(from);
      let path = `/previews/${pair.id}/a`;
      if (scenario === 'invalid-id') {
        path = '/previews/not-a-uuid/a';
      }
      if (scenario === 'side') {
        path = `/previews/${pair.id}/c`;
      }
      if (scenario === 'unknown') {
        path = `/previews/${crypto.randomUUID()}/a`;
      }
      if (scenario === 'actor') {
        from = await admitted('192.0.2.2');
      }
      if (scenario === 'render') {
        await env.DB.prepare("UPDATE publications SET render = 'text' WHERE family = ?")
          .bind(HTML)
          .run();
      }
      if (scenario === 'publication') {
        await publishHtml('replacement-publication');
      }
      if (scenario === 'cookie') {
        from = browser();
      }

      const response = await call(from, 'GET', path);

      expect(response.status).toBe(code);
      expectIsolation(response);
      expect(await response.text()).toBe(unavailable);
    },
  );

  test.each(['vote', 'skip'] as const)(
    'denies an old preview after %s without falling back to the next pending pair',
    async (operation) => {
      await publishHtml();
      const from = await admitted();
      const first = await htmlPair(from);
      const url = `/previews/${first.id}/a`;
      const original = await (await call(from, 'GET', url)).text();

      const changed =
        operation === 'vote'
          ? await vote(from, first.id)
          : await call(from, 'POST', '/pair/skip', { pair: first.id });
      expect(changed.status).toBe(operation === 'vote' ? 204 : 200);
      const following = await htmlPair(from);
      const response = await call(from, 'GET', url);

      expect(following.id).not.toBe(first.id);
      expect(response.status).toBe(404);
      expectIsolation(response);
      expect(await response.text()).toBe(unavailable);
      if (operation === 'vote') {
        const undone = await call(from, 'POST', '/votes/undo', { family: HTML });
        expect(undone.status).toBe(204);
        expect((await htmlPair(from)).id).toBe(first.id);
        const restored = await call(from, 'GET', url);
        expect(restored.status).toBe(200);
        expect(await restored.text()).toBe(original);
      }
    },
  );

  test('rejects a concurrent superseded draw and serves only the new publication', async () => {
    await publishHtml();
    const from = await admitted();
    const prepare = env.DB.prepare.bind(env.DB);
    let replacement: string | undefined;
    let switched = false;
    vi.spyOn(env.DB, 'prepare').mockImplementation((query) => {
      const statement = prepare(query);
      if (query.includes('system IN (?, ?)')) {
        const bind = statement.bind.bind(statement);
        vi.spyOn(statement, 'bind').mockImplementation((...values) => {
          const bound = bind(...values);
          const all = bound.all.bind(bound);
          vi.spyOn(bound, 'all').mockImplementation(async <T>() => {
            const result = await all<T>();
            if (!switched) {
              switched = true;
              await publishHtml('replacement-publication', 'New ');
              replacement = (await htmlPair(from)).id;
            }
            return result;
          });
          return bound;
        });
      }
      return statement;
    });

    const response = await call(from, 'GET', `/pair?family=${HTML}`);

    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ error: 'stale_pair' });
    expect(replacement).toEqual(expect.any(String));
    expect((await htmlPair(from)).id).toBe(replacement);
    for (const side of ['a', 'b']) {
      const preview = await call(from, 'GET', `/previews/${replacement}/${side}`);
      expect(preview.status).toBe(200);
      expect(await preview.text()).toContain('<title>New ');
    }
  });
});
