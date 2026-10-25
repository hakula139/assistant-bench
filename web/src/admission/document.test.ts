import { createHash } from 'node:crypto';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

import { JSDOM } from 'jsdom';
import { afterAll, beforeAll, describe, expect, test } from 'vitest';

let directory: string;
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

describe('built admission document', () => {
  beforeAll(async () => {
    directory = await mkdtemp(join(tmpdir(), 'bench-admission-'));
    await promisify(execFile)('pnpm', ['exec', 'astro', 'build', '--outDir', directory], {
      cwd: fileURLToPath(new URL('../../', import.meta.url)),
    });
    const html = await readFile(join(directory, 'admission-shell/index.html'), 'utf8');
    document = new JSDOM(html, { url: 'https://app.example.com/admit' }).window.document;
    const meta = document.querySelector<HTMLMetaElement>(
      'meta[http-equiv="Content-Security-Policy"]',
    );
    if (meta === null) {
      throw new Error('Expected the generated admission policy');
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
  }, 60_000);

  afterAll(async () => {
    await rm(directory, { recursive: true, force: true });
  });

  test('provides one inert JSON slot inside the shared accessible shell', () => {
    const data = document.querySelector<HTMLScriptElement>('#admission-data');

    expect(document.title).toContain('Before you vote');
    expect(document.querySelector('main h1')?.textContent).toBe('Before you vote');
    expect(document.querySelector('header nav')).not.toBeNull();
    expect(document.querySelector('noscript')?.textContent).toContain('Enable JavaScript');
    expect(document.querySelector('#admission-status')?.getAttribute('aria-live')).toBe('polite');
    expect(document.querySelector('#admission-challenge')?.getAttribute('aria-busy')).toBe('true');
    expect(data?.type).toBe('application/json');
    expect(JSON.parse(data?.textContent ?? '')).toBe('__BENCH_ADMISSION_DATA__');
    expect(document.documentElement.outerHTML.match(/"__BENCH_ADMISSION_DATA__"/g)).toHaveLength(1);
  });

  test('allows its emitted client and governed theme script under the generated policy', async () => {
    const sources = policy.get('script-src') ?? policy.get('default-src') ?? [];
    const scripts = [...document.querySelectorAll<HTMLScriptElement>('script')].filter(
      (script) => script.type !== 'application/json',
    );
    const client = scripts.find(
      (script) => new URL(script.src || document.URL).pathname === '/admission.js',
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
    if (client === undefined || theme === undefined || initializer === undefined) {
      throw new Error('Expected the built admission client and shared theme initializer');
    }

    expect(client.type).toBe('module');
    expect(
      Boolean(initializer.compareDocumentPosition(meta) & initializer.DOCUMENT_POSITION_FOLLOWING),
    ).toBe(true);
    expect(await readFile(join(directory, 'admission.js'), 'utf8')).toContain('/admission/replay');
    expect(allowsScript(client, sources)).toBe(true);
    expect(allowsScript(theme, sources)).toBe(true);
    expect(
      allowsScript(
        client,
        sources.filter((source) => source !== "'self'"),
      ),
    ).toBe(false);
    expect(
      allowsScript(
        theme,
        sources.filter((source) => !source.startsWith("'sha256-")),
      ),
    ).toBe(false);
    for (const script of governed) {
      expect(allowsScript(script, sources), script.src || 'inline script').toBe(true);
    }
    expect(sources).toContain('https://challenges.cloudflare.com');
    expect(sources).not.toContain("'unsafe-inline'");
    expect(sources).not.toContain("'unsafe-eval'");
  });

  test('serves actual shared styles and permits the stylesheet and challenge frame', async () => {
    const styles = [...document.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"]')];
    expect(styles.length).toBeGreaterThan(0);
    for (const style of styles) {
      const url = new URL(style.href);
      expect(url.origin).toBe(new URL(document.URL).origin);
      expect(await readFile(join(directory, url.pathname), 'utf8')).toContain('--background');
    }
    expect(policy.get('style-src')).toContain("'self'");
    expect(policy.get('frame-src')).toEqual(['https://challenges.cloudflare.com']);
    expect(policy.get('connect-src')).toEqual(["'self'"]);
    expect(policy.get('default-src')).toEqual(["'none'"]);
  });
});
import { execFile } from 'node:child_process';
