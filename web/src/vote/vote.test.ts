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

describe('built vote document', () => {
  beforeAll(async () => {
    directory = await mkdtemp(join(tmpdir(), 'bench-vote-'));
    await promisify(execFile)('pnpm', ['exec', 'astro', 'build', '--outDir', directory], {
      cwd: fileURLToPath(new URL('../../', import.meta.url)),
    });
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
  }, 60_000);

  afterAll(async () => {
    await rm(directory, { recursive: true, force: true });
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
      await readFile(fileURLToPath(new URL('../../public/arena-families.json', import.meta.url))),
    );
    expect(document.title).toContain('Vote');
    expect(document.querySelector('header nav')).not.toBeNull();
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
import { execFile } from 'node:child_process';
