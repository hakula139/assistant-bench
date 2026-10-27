import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtemp, readdir, readFile, rm, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

import AxeBuilder from '@axe-core/playwright';
import { JSDOM } from 'jsdom';
import { chromium, type Browser } from 'playwright';
import { afterAll, beforeAll, describe, expect, test } from 'vitest';

import { makeSession, VOTE_FAMILY } from '@/test/arena';

const root = fileURLToPath(new URL('../../', import.meta.url));
let directory: string;

beforeAll(async () => {
  directory = await mkdtemp(join(tmpdir(), 'bench-site-'));
  await promisify(execFile)('pnpm', ['exec', 'astro', 'build', '--outDir', directory], {
    cwd: root,
  });
}, 180_000);

afterAll(async () => {
  await rm(directory, { recursive: true, force: true });
});

describe('built vote document', () => {
  let document: Document;
  let policy: Map<string, string[]>;

  function allowsScript(script: HTMLScriptElement, sources: readonly string[]): boolean {
    if (script.src !== '') {
      const url = new URL(script.src);
      return (
        sources.includes(url.origin) ||
        (url.origin === new URL(document.URL).origin && sources.includes("'self'"))
      );
    }

    const hash = createHash('sha256').update(script.textContent).digest('base64');
    return sources.includes(`'sha256-${hash}'`);
  }

  beforeAll(async () => {
    const html = await readFile(join(directory, 'vote/index.html'), 'utf8');
    document = new JSDOM(html, { url: 'https://app.example.com/vote/' }).window.document;
    const meta = document.querySelector<HTMLMetaElement>(
      'meta[http-equiv="Content-Security-Policy"]',
    );
    if (meta === null) {
      throw new Error('Expected the generated vote policy');
    }
    policy = new Map(
      meta.content
        .split(';')
        .filter((directive) => directive.trim() !== '')
        .map((directive) => {
          const [name, ...sources] = directive.trim().split(/\s+/);
          return [name, sources];
        }),
    );
  });

  test('allows the emitted vote island, shared theme and managed provider under its script policy', () => {
    const sources = policy.get('script-src') ?? policy.get('default-src') ?? [];
    const scripts = [...document.querySelectorAll<HTMLScriptElement>('script')].filter(
      (script) => script.type !== 'application/json',
    );
    const meta = document.querySelector('meta[http-equiv="Content-Security-Policy"]');
    if (meta === null) {
      throw new Error('Expected the generated policy');
    }
    const governed = scripts.filter((script) =>
      Boolean(meta.compareDocumentPosition(script) & meta.DOCUMENT_POSITION_FOLLOWING),
    );
    const theme = governed.find((script) => script.textContent.includes('dataset.theme'));
    const initializer = scripts.find((script) => script.textContent.includes('dataset.theme'));
    if (theme === undefined || initializer === undefined) {
      throw new Error('Expected the shared theme initializer');
    }

    expect(
      Boolean(initializer.compareDocumentPosition(meta) & initializer.DOCUMENT_POSITION_FOLLOWING),
    ).toBe(true);
    expect(allowsScript(theme, sources)).toBe(true);
    expect(
      allowsScript(
        theme,
        sources.filter((source) => !source.startsWith("'sha256-")),
      ),
    ).toBe(false);
    for (const script of governed) {
      expect(allowsScript(script, sources), script.src || 'inline script').toBe(true);
    }
    const island = document.querySelector('astro-island[component-export="VoteApp"]');
    const client = new URL(island?.getAttribute('component-url') ?? '', document.URL);
    expect(client.pathname).toMatch(/^\/_astro\/.+\.js$/);
    expect(sources.includes("'self'") || sources.includes(client.origin)).toBe(true);
    expect(sources).toContain('https://challenges.cloudflare.com');
    expect(sources).not.toContain("'unsafe-inline'");
    expect(sources).not.toContain("'unsafe-eval'");
  });

  test('serves the shared shell, styles and public catalog with runtime frame policy', async () => {
    expect(await readFile(join(directory, 'arena-families.json'))).toEqual(
      await readFile(join(root, 'public/arena-families.json')),
    );
    expect(document.title).toContain('Vote');
    expect(document.head.querySelector('meta[name="robots"]')?.getAttribute('content')).toBe(
      'noindex',
    );
    expect(document.querySelector('header nav')).not.toBeNull();
    expect(document.querySelector('a[href="#main"]')?.textContent.trim()).toBe('Skip to content');
    const island = document.querySelector('main#main astro-island[component-export="VoteApp"]');
    expect(island).not.toBeNull();
    expect(document.querySelector('footer')).toBeNull();
    const styles = [...document.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"]')];
    expect(styles.length).toBeGreaterThan(0);
    for (const style of styles) {
      const url = new URL(style.href);
      expect(url.origin).toBe(new URL(document.URL).origin);
      expect(await readFile(join(directory, url.pathname), 'utf8')).toContain('--background');
    }
    expect(policy.get('style-src')).toContain("'self'");
    expect(policy.has('frame-src')).toBe(false);
    await expect(readFile(join(directory, 'admission-shell/index.html'))).rejects.toMatchObject({
      code: 'ENOENT',
    });
    await expect(readFile(join(directory, 'admission.js'))).rejects.toMatchObject({
      code: 'ENOENT',
    });
  });
});

describe('built pages', () => {
  const origin = 'http://site.example';
  const vote = '/vote/index.html';
  const api = new Map<string, unknown>([
    ['GET /api/status', { status: 'ready' }],
    ['GET /api/families', [VOTE_FAMILY]],
    ['POST /api/sessions', makeSession()],
  ]);
  let pages: string[];
  let browser: Browser;

  beforeAll(async () => {
    pages = (await readdir(directory, { recursive: true }))
      .filter((path) => path.endsWith('.html'))
      .map((path) => `/${path}`)
      .sort();
    browser = await chromium.launch();
  }, 60_000);

  afterAll(async () => {
    await browser.close();
  });

  test.each(['light', 'dark'] as const)(
    "pass axe-core's default rules and target-size in the %s theme, without errors, once their islands hydrate",
    async (theme) => {
      const context = await browser.newContext({ colorScheme: theme });
      await context.route('**', (route) => route.abort());
      await context.route(`${origin}/**`, async (route) => {
        const request = route.request();
        const { pathname } = new URL(request.url());
        const reply = api.get(`${request.method()} ${pathname}`);
        if (reply !== undefined) {
          await route.fulfill({ json: reply });
          return;
        }

        const file = join(directory, pathname);
        const found = await stat(file).then(
          (stats) => stats.isFile(),
          () => false,
        );
        await route.fulfill(found ? { path: file } : { status: 404 });
      });
      const problems: Record<string, string[]> = {};

      for (const path of pages) {
        const page = await context.newPage();
        const errors: string[] = [];
        page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
        page.on('console', (message) => {
          if (message.type() === 'error') {
            errors.push(`console: ${message.text()}`);
          }
        });

        await page.goto(`${origin}${path}${path === vote ? `?family=${VOTE_FAMILY.name}` : ''}`);
        // Charts hydrate only once visible, so the viewport takes the whole page.
        await page.setViewportSize({
          width: 1280,
          height: await page.evaluate(() => document.documentElement.scrollHeight),
        });
        await page.waitForFunction(() => document.querySelector('astro-island[ssr]') === null);
        if (path === vote) {
          await page
            .getByRole('button', { name: 'Issues' })
            .first()
            .click({ timeout: 10_000 })
            .catch(async (error: unknown) => {
              const shown = await page.locator('main').innerText();
              throw new Error(`The vote page shows no pair: ${shown}\n${errors.join('\n')}`, {
                cause: error,
              });
            });
          await page.getByRole('dialog', { name: 'Issues in output A' }).waitFor();
        }
        await page.waitForLoadState('networkidle');

        expect(await page.evaluate(() => document.documentElement.dataset.theme), path).toBe(theme);
        const results = await new AxeBuilder({ page })
          .options({ rules: { 'target-size': { enabled: true } } })
          .analyze();
        const found = [
          ...errors,
          ...results.violations.flatMap(({ id, nodes }) =>
            nodes.map(({ target }) => `${id}: ${target.join(' ')}`),
          ),
        ];
        if (found.length > 0) {
          problems[path] = found;
        }
        await page.close();
      }
      await context.close();

      expect(pages).toEqual(expect.arrayContaining(['/index.html', vote]));
      expect(problems).toEqual({});
    },
    120_000,
  );
});
