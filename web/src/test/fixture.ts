import { reportSchema, type Report } from '@/lib/report';
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

const ENTITIES: Record<string, string> = {
  '&amp;': '&',
  '&lt;': '<',
  '&gt;': '>',
  '&#39;': "'",
  '&quot;': '"',
};

/** Undoes Astro's `escapeHTML`, which escapes only these five characters. */
function unescapeAttribute(value: string): string {
  return value.replace(/&(?:amp|lt|gt|#39|quot);/g, (entity) => ENTITIES[entity]);
}

/**
 * Calls `visit` on every object in the page's serialized island props, where Astro writes each
 * prop as a `[type, value]` pair and type 0 marks a plain value.
 */
export function visitIslandProps(html: string, visit: (value: object) => void) {
  const walk = (value: unknown) => {
    if (Array.isArray(value)) {
      value.forEach(walk);
    } else if (typeof value === 'object' && value !== null) {
      visit(value);
      Object.values(value).forEach(walk);
    }
  };

  for (const [, props] of html.matchAll(/<astro-island\b[^>]*\bprops="([^"]*)"/g)) {
    walk(JSON.parse(unescapeAttribute(props)));
  }
}

/** Chart labels render client-side, so inspect their serialized props. */
function islandLabels(html: string): string[] {
  const labels: string[] = [];
  visitIslandProps(html, (value) => {
    if ('label' in value && Array.isArray(value.label) && typeof value.label[1] === 'string') {
      labels.push(value.label[1]);
    }
  });

  return labels;
}

/**
 * Returns the table names that a page shows as text, in a `title`, `aria-label`, `alt`, or meta
 * `content` attribute, or as an island's chart label, where only display names belong. Links and
 * other island props keep table names.
 */
export function shownSlugs(html: string, names: SystemNames = FIXTURE_NAMES): string[] {
  const labels = [...html.matchAll(/\b(?:title|aria-label|alt|content)="([^"]*)"/g)].map(
    (match) => match[1],
  );
  const shown = [html.replace(/<[^>]+>/g, ' '), ...labels, ...islandLabels(html)].join('\n');
  // The slug as a whole token, outside any longer hyphenated word.
  return Object.keys(names).filter((slug) =>
    new RegExp(`(?<![\\w-])${RegExp.escape(slug)}(?![\\w-])`).test(shown),
  );
}

const fixtures = import.meta.glob<Record<string, unknown>>(
  '../../../tests/fixtures/{report,report-early,report-tests,report.schema}.json',
  { import: 'default' },
);

/** Each result is an independent mutable copy. */
export async function readFixture(
  name: 'report' | 'report-early' | 'report-tests' | 'report.schema' = 'report',
): Promise<Record<string, unknown>> {
  return structuredClone(await fixtures[`../../../tests/fixtures/${name}.json`]());
}

/** A report whose results the named scorer wrote. */
type ScoredReport<S extends Report['results']['scorer']> = Report & {
  results: Extract<Report['results'], { scorer: S }>;
};

/** Parses a report fixture, refusing one that another scorer wrote. */
export async function readReport<S extends Report['results']['scorer']>(
  name: 'report' | 'report-early' | 'report-tests',
  scorer: S,
): Promise<ScoredReport<S>> {
  const report = reportSchema.parse(await readFixture(name));
  if (report.results.scorer !== scorer) {
    throw new Error(`${name} is not a ${scorer} report`);
  }
  return report as ScoredReport<S>;
}
