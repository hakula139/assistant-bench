import { readFile } from 'node:fs/promises';

import type { SystemNames } from '@/lib/systems';

/** Display names for every system the fixtures name, each unlike its table name. */
export const FIXTURE_NAMES: SystemNames = {
  'acme-edge': 'Acme Edge',
  'acme-flash': 'Acme Flash',
  'acme-large': 'Acme Large',
  'acme-mini': 'Acme Mini',
  'acme-nano': 'Acme Nano',
  'acme-new': 'Acme New',
  'acme-pro': 'Acme Pro',
  'acme-small': 'Acme Small',
  'acme-tiny': 'Acme Tiny',
};

/**
 * Returns the table names that a page shows as text or in a `title`, `aria-label`, `alt`, or meta
 * `content` attribute, where only display names belong. Links and island props keep table names.
 */
export function shownSlugs(html: string, names: SystemNames = FIXTURE_NAMES): string[] {
  const labels = [...html.matchAll(/\b(?:title|aria-label|alt|content)="([^"]*)"/g)].map(
    (match) => match[1],
  );
  const shown = [html.replace(/<[^>]+>/g, ' '), ...labels].join('\n');
  // The slug as a whole token, outside any longer hyphenated word.
  return Object.keys(names).filter((slug) =>
    new RegExp(`(?<![\\w-])${RegExp.escape(slug)}(?![\\w-])`).test(shown),
  );
}

/**
 * Reads a committed fixture as raw JSON, so each test can alter its own copy: `report` after ten
 * simulated sessions, `report-early` after the first, which has no decision, or `report.schema`.
 * The pydantic models write all three, and a pytest fails when one drifts from them.
 */
export async function readFixture(
  name: 'report' | 'report-early' | 'report.schema' = 'report',
): Promise<Record<string, unknown>> {
  const path = new URL(`../../../tests/fixtures/${name}.json`, import.meta.url);
  return JSON.parse(await readFile(path, 'utf8')) as Record<string, unknown>;
}
