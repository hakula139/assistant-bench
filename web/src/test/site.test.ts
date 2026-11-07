import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import {
  cp,
  mkdir,
  mkdtemp,
  readdir,
  readFile,
  rm,
  stat,
  symlink,
  writeFile,
} from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

import AxeBuilder from '@axe-core/playwright';
import { JSDOM } from 'jsdom';
import { chromium, type Browser } from 'playwright';
import { afterAll, beforeAll, describe, expect, test } from 'vitest';

import { makeState, VOTE_FAMILY } from '@/test/arena';
import { readReport } from '@/test/fixture';

const root = fileURLToPath(new URL('../../', import.meta.url));
const origin = 'https://site.example';
const repository = 'https://forge.example.com/team/project';
const base = '/team/project/mr-7/';
let directory: string;
let prefixed: string;
let fixture: string;
let browser: Browser | undefined;

beforeAll(async () => {
  // Vitest sets `NODE_ENV=test`, under which Vite would bundle React's development build, and a
  // `BASE_URL` that would override the configured base.
  const env: NodeJS.ProcessEnv = { ...process.env, NODE_ENV: 'production' };
  delete env.BASE_URL;
  delete env.BENCH_NIXOS_CONFIG;
  delete env.CI_PAGES_URL;
  delete env.CI_PROJECT_URL;
  fixture = await mkdtemp(join(tmpdir(), 'bench-site-'));
  const site = join(fixture, 'web');
  await mkdir(join(site, 'node_modules'), { recursive: true });
  await Promise.all(
    ['src', 'astro.config.ts', 'package.json', 'tsconfig.json'].map((entry) =>
      cp(join(root, entry), join(site, entry), { recursive: true }),
    ),
  );
  await symlink(join(root, 'public'), join(site, 'public'));
  for (const entry of ['families', 'systems.toml']) {
    await symlink(join(root, '..', entry), join(fixture, entry));
  }
  for (const entry of await readdir(join(root, 'node_modules'))) {
    if (!entry.startsWith('.')) {
      await symlink(join(root, 'node_modules', entry), join(site, 'node_modules', entry));
    }
  }
  const report = await readReport('report', 'votes');
  report.family = 'writing-zh';
  const reports = join(fixture, 'reports', report.family);
  await mkdir(reports, { recursive: true });
  await writeFile(join(reports, `${report.round}.json`), JSON.stringify(report));

  const build = (outDir: string, extra: NodeJS.ProcessEnv = {}) =>
    promisify(execFile)(
      process.execPath,
      [join(root, 'node_modules/astro/bin/astro.mjs'), 'build', '--outDir', outDir],
      { cwd: site, env: { ...env, ...extra } },
    );
  directory = join(fixture, 'dist');
  prefixed = join(fixture, 'prefixed');
  await build(directory);
  // GitLab's `CI_PAGES_URL` carries no trailing slash.
  await build(prefixed, {
    CI_PAGES_URL: `${origin}${base.slice(0, -1)}`,
    CI_PROJECT_URL: repository,
  });
  browser = await chromium.launch();
}, 180_000);

afterAll(async () => {
  await browser?.close();
  if (fixture) {
    await rm(fixture, { recursive: true, force: true });
  }
}, 60_000);

test("bundles React's production build", async () => {
  const scripts = (await readdir(directory, { recursive: true })).filter((path) =>
    path.endsWith('.js'),
  );
  const sources = await Promise.all(scripts.map((path) => readFile(join(directory, path), 'utf8')));

  expect(scripts.length).toBeGreaterThan(0);
  expect(
    scripts.filter((_, index) => sources[index].includes('Download the React DevTools')),
  ).toEqual([]);
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

  test('serves the shared shell and styles, leaving the frame policy to the response headers', async () => {
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
  });
});

describe('built pages', () => {
  const vote = '/vote/index.html';
  const api = new Map<string, unknown>([
    ['GET /api/families', { families: [VOTE_FAMILY] }],
    ['GET /api/freshness', { status: 'current' }],
    ['GET /api/pair', makeState()],
  ]);
  let pages: string[];

  beforeAll(async () => {
    pages = (await readdir(directory, { recursive: true }))
      .filter((path) => path.endsWith('.html'))
      .map((path) => `/${path}`)
      .sort();
  }, 60_000);

  test.each(['light', 'dark'] as const)(
    "pass axe-core's default rules and target-size in the %s theme, without errors, once their islands hydrate",
    async (theme) => {
      assert(browser);
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

        await page.goto(`${origin}${path}${path === vote ? `?family=${VOTE_FAMILY.family}` : ''}`);
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

      expect(pages).toEqual(
        expect.arrayContaining(['/index.html', '/writing/zh/acme-large/index.html', vote]),
      );
      expect(problems).toEqual({});
    },
    120_000,
  );
});

describe('Pages deployment', () => {
  test('serves pages and assets below its base without the vote page', async () => {
    assert(browser);
    const context = await browser.newContext();
    const fetched = new Set<string>();
    const missing: string[] = [];
    const errors: string[] = [];
    await context.route('**', (route) => route.abort());
    await context.route(`${origin}/**`, async (route) => {
      const { pathname } = new URL(route.request().url());
      fetched.add(pathname);
      if (!pathname.startsWith(base)) {
        missing.push(pathname);
        await route.fulfill({ status: 404 });
        return;
      }
      const file = join(
        prefixed,
        pathname.slice(base.length),
        pathname.endsWith('/') ? 'index.html' : '',
      );
      const found = await stat(file).then(
        (stats) => stats.isFile(),
        () => false,
      );
      if (!found) {
        missing.push(pathname);
      }
      await route.fulfill(found ? { path: file } : { status: 404 });
    });
    const page = await context.newPage();
    page.on('pageerror', (error) => errors.push(error.message));

    try {
      await page.goto(`${origin}${base}`);
      await page.waitForLoadState('networkidle');

      const navigation = page.getByRole('navigation', { name: 'Main' });
      expect(
        await navigation.getByRole('link', { name: 'Leaderboards' }).getAttribute('href'),
      ).toBe(base);
      expect(await navigation.getByRole('link', { name: 'Methodology' }).getAttribute('href')).toBe(
        `${base}methodology/`,
      );
      expect(await navigation.getByRole('link', { name: 'Vote' }).count()).toBe(0);
      expect(await stat(join(prefixed, 'vote')).catch(() => null)).toBeNull();
      expect(await page.locator('link[rel="icon"]').getAttribute('href')).toBe(
        `${base}favicon.svg`,
      );
      expect(await page.locator('footer a').getAttribute('href')).toBe(repository);
      const favicon = await page.evaluate(async (url) => {
        const response = await fetch(url);
        return { status: response.status, body: await response.text() };
      }, `${origin}${base}favicon.svg`);
      expect(favicon.status).toBe(200);
      expect(favicon.body).toContain('<svg');

      const board = page.locator(`a[href="${base}writing/en/"]`).first();
      await board.click();
      expect(new URL(page.url()).pathname).toBe(`${base}writing/en/`);
      expect(
        await page.getByRole('link', { name: 'Cases', exact: true }).getAttribute('href'),
      ).toBe(`${repository}/-/tree/main/families/writing-en`);

      await navigation.getByRole('link', { name: 'Methodology' }).click();
      expect(new URL(page.url()).pathname).toBe(`${base}methodology/`);
      expect(
        await page.locator(`a[href^="${repository}/-/blob/main/docs/design/"]`).count(),
      ).toBeGreaterThan(0);

      expect(
        [...fetched].some((path) => path.startsWith(`${base}_astro/`) && path.endsWith('.css')),
      ).toBe(true);
      expect(
        [...fetched].some((path) => path.startsWith(`${base}_astro/`) && path.endsWith('.woff2')),
      ).toBe(true);
      expect(
        [...fetched].some((path) => path.startsWith(`${base}_astro/`) && path.endsWith('.js')),
      ).toBe(true);
      expect(missing).toEqual([]);
      expect(errors).toEqual([]);
    } finally {
      await context.close();
    }
  }, 60_000);
});
