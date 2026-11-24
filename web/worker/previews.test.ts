import { env } from 'cloudflare:workers';
import { describe, expect, test } from 'vitest';

import { PREVIEW_UNAVAILABLE } from './previews';
import {
  admitted,
  browser,
  call,
  duringPlacement,
  expectIsolation,
  pairOf,
  publish,
  vote,
  type Browser,
} from './test/support';

const HTML = 'frontend-fixture';
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

  test.each<
    readonly [string, number, (pairId: string) => Promise<{ from?: Browser; path?: string }>]
  >([
    ['an invalid UUID', 400, () => Promise.resolve({ path: '/previews/not-a-uuid/a' })],
    ['an invalid side', 400, (id) => Promise.resolve({ path: `/previews/${id}/c` })],
    ['an unknown pair', 404, () => Promise.resolve({ path: `/previews/${crypto.randomUUID()}/a` })],
    ['a different actor', 404, async () => ({ from: await admitted('192.0.2.2') })],
    [
      'a text publication',
      404,
      async () => {
        await env.DB.prepare("UPDATE publications SET render = 'text' WHERE family = ?")
          .bind(HTML)
          .run();
        return {};
      },
    ],
    [
      'an inactive publication',
      404,
      async () => {
        await publishHtml('replacement-publication');
        return {};
      },
    ],
    ['no actor cookie', 401, () => Promise.resolve({ from: browser() })],
  ])('rejects %s with the same safe body and isolation headers', async (_, code, arrange) => {
    await publishHtml();
    const owner = await admitted();
    const pair = await htmlPair(owner);
    const { from = owner, path = `/previews/${pair.id}/a` } = await arrange(pair.id);

    const response = await call(from, 'GET', path);

    expect(response.status).toBe(code);
    expectIsolation(response);
    expect(await response.text()).toBe(PREVIEW_UNAVAILABLE);
  });

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
      expect(await response.text()).toBe(PREVIEW_UNAVAILABLE);
      if (operation === 'vote') {
        const undone = await call(from, 'POST', '/votes/undo', { family: HTML, pair: first.id });
        expect(undone.status).toBe(204);
        const fresh = await htmlPair(from);
        expect(fresh.id).not.toBe(first.id);
        expect((await call(from, 'GET', url)).status).toBe(404);
        const restored = await call(from, 'GET', `/previews/${fresh.id}/a`);
        expect(restored.status).toBe(200);
        expectIsolation(restored);
        expect(await restored.text()).toBe(original);
      }
    },
  );

  test('rejects a concurrent superseded draw and serves only the new publication', async () => {
    await publishHtml();
    const from = await admitted();
    let replacement: string | undefined;
    duringPlacement(async () => {
      await publishHtml('replacement-publication', 'New ');
      replacement = (await htmlPair(from)).id;
    });

    const response = await call(from, 'GET', `/pair?family=${HTML}`);

    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({ error: 'stale_pair' });
    expect(replacement).toEqual(expect.any(String));
    const pair = await htmlPair(from);
    expect(pair.id).toBe(replacement);
    for (const side of ['a', 'b']) {
      const preview = await call(from, 'GET', `/previews/${pair.id}/${side}`);
      expect(preview.status).toBe(200);
      expect(await preview.text()).toContain('<title>New ');
    }
  });
});
