import { getContainerRenderer } from '@astrojs/react/container-renderer';
import { experimental_AstroContainer as AstroContainer } from 'astro/container';
import { loadRenderers } from 'astro:container';
import { describe, expect, test, vi } from 'vitest';

import FamilyPanel from '@/components/site/FamilyPanel.astro';
import Header from '@/components/site/Header.astro';
import type { FamilyConfig } from '@/lib/family';
import { reportSchema } from '@/lib/report';
import { votesRounds, type Family, type VotesRound } from '@/lib/reports';
import FamilyPage from '@/pages/[family]/index.astro';
import SystemPage from '@/pages/[family]/[system].astro';
import HomePage from '@/pages/index.astro';
import MethodologyPage from '@/pages/methodology.astro';
import { FIXTURE_NAMES, readFixture, shownSlugs } from '@/test/fixture';

/** The families the header lists, since the content collections stay empty under Vitest. */
const listed = vi.hoisted(() => ({ families: [] as Family[] }));
vi.mock(import('@/lib/reports'), async (importOriginal) => ({
  ...(await importOriginal()),
  getFamilies: () => Promise.resolve(listed.families),
}));

/** The fixture's systems and the family itself, whose table and directory names pages hide. */
const SLUGS = { ...FIXTURE_NAMES, arena: 'Arena Writing' };

const arenaConfig: FamilyConfig = {
  scorer: 'votes',
  display_name: 'Arena Writing',
  systems: ['acme-large', 'acme-new', 'acme-pro', 'acme-small'],
  reference: 'acme-large',
  render: 'text',
  language: 'zh',
  gates: { min_chars: 40, max_chars: 6000, script: 'hans' },
  public: true,
};

/** The fixture's round, after an earlier one in which every system scored 900. */
async function family(): Promise<Family> {
  const latest = reportSchema.parse(await readFixture());
  const earlier = structuredClone(latest);
  earlier.round = '2026-10-07';
  earlier.closed_at = '2026-10-07T18:00:00Z';
  if (earlier.results.scorer !== 'votes') {
    throw new Error('the fixture is a votes report');
  }
  for (const standing of earlier.results.standings) {
    standing.score = 900;
  }
  return {
    name: 'arena',
    config: arenaConfig,
    rounds: [earlier, latest],
    systemNames: FIXTURE_NAMES,
  };
}

function systemProps(system: string, rounds: VotesRound[]) {
  return {
    family: 'arena',
    familyName: arenaConfig.display_name,
    system,
    rounds,
    names: FIXTURE_NAMES,
  };
}

async function render(
  page: Parameters<AstroContainer['renderToString']>[0],
  props: Record<string, unknown>,
  path = '/',
): Promise<string> {
  const container = await AstroContainer.create({
    renderers: await loadRenderers([getContainerRenderer()]),
  });
  return container.renderToString(page, {
    props,
    request: new Request(new URL(path, 'https://bench.test')),
  });
}

const text = (html: string) =>
  html
    .replace(/<[^>]+>/g, ' ')
    .replaceAll('&#x27;', "'")
    .replace(/\s+/g, ' ');

/** A family scored by tests, with one round that lists the weaker system first. */
function testsFamily(): Family {
  const report = reportSchema.parse({
    family: 'smoke',
    round: '2026-10-21',
    closed_at: '2026-10-21T18:00:00Z',
    results: {
      scorer: 'tests',
      standings: [
        {
          system: 'acme-small',
          fingerprint: null,
          trials: 4,
          errored: 0,
          pass_rate: 0.25,
          mean_cost_usd: null,
          p50_latency_sec: null,
        },
        {
          system: 'acme-pro',
          fingerprint: null,
          trials: 4,
          errored: 1,
          pass_rate: 0.75,
          mean_cost_usd: null,
          p50_latency_sec: null,
        },
      ],
    },
  });
  return {
    name: 'smoke',
    config: {
      scorer: 'tests',
      display_name: 'Smoke Tasks',
      systems: ['acme-pro', 'acme-small'],
      public: true,
    },
    rounds: [report],
    systemNames: FIXTURE_NAMES,
  };
}

/** The methodology section a family page's "How to read this board" link names. */
function guide(html: string): string {
  const match = /href="\/methodology\/#([\w-]+)"/.exec(html);
  if (!match) {
    throw new Error('the family page links no methodology section');
  }
  return match[1];
}

describe('family page', () => {
  test("draws the family's gates and reference, then the round summary", async () => {
    const html = await render(FamilyPage, { family: await family() });

    expect(text(html)).toContain(
      'Gates finished, not blank, 40–6,000 characters, in Chinese, Simplified script',
    );
    expect(text(html)).toContain('Scores Bradley-Terry, reference at 1,000, 95% intervals');
    expect(text(html)).toContain(
      'Leader Acme Large tied with the top score, chosen by the lower cost',
    );
    expect(text(html)).toContain('1–3 Acme Pro 1,004 955–1,065 114');
  });

  test('lists the systems with unbounded intervals before the first round closes', async () => {
    const empty: Family = { ...(await family()), rounds: [] };

    const html = await render(FamilyPage, { family: empty });

    expect(text(html)).toContain('No round has closed yet.');
    expect(text(html)).toContain('Acme Large Fixed at 1,000 as the reference 1,000');
    expect(text(html)).toContain('Acme Pro Unbounded no votes');
    expect(html).not.toContain('Leader');
  });

  test('keys the empty board to the marks it draws', async () => {
    const empty: Family = { ...(await family()), rounds: [] };

    const html = await render(FamilyPage, { family: empty });

    expect(text(html)).toContain('Reference, 1,000 Unbounded');
    expect(html).not.toContain('Score, 95% interval');
  });

  test('names the output a family renders as pages', async () => {
    const pages: Family = {
      ...(await family()),
      config: { ...arenaConfig, render: 'html' },
      rounds: [],
    };

    const html = await render(FamilyPage, { family: pages });

    expect(text(html)).toContain('Run an HTML page per case');
  });

  test("draws a tests family's steps and its pass rates", async () => {
    const html = await render(FamilyPage, { family: testsFamily() });

    expect(text(html)).toContain(
      "Run one trial per task Tests the task's own tests Pass rate errored trials fail",
    );
    expect(text(html)).toContain('Acme Pro 75% 4 1');
  });

  test.each([
    ['with rounds', family],
    ['before its first round', async () => ({ ...(await family()), rounds: [] })],
    ['scored by tests', () => Promise.resolve(testsFamily())],
  ])('names the family and its systems by display name %s', async (_, load) => {
    const loaded = await load();

    const html = await render(FamilyPage, { family: loaded });

    expect(html).toContain(`<title>${loaded.config.display_name} | HAKULA†BENCH</title>`);
    expect(text(html)).toContain(`${loaded.config.display_name} Run `);
    expect(shownSlugs(html, { ...SLUGS, smoke: 'Smoke Tasks' })).toEqual([]);
  });

  test.each([
    ['votes', family],
    ['tests', () => Promise.resolve(testsFamily())],
  ])("links the %s board to its scorer's methodology section", async (scorer, load) => {
    const methodology = await render(MethodologyPage, {});

    const html = await render(FamilyPage, { family: await load() });

    expect(guide(html)).toBe(scorer);
    expect(methodology).toContain(`id="${scorer}"`);
  });
});

describe('home page', () => {
  test("keys the shared legend to the marks the families' panels draw", async () => {
    listed.families = [{ ...(await family()), rounds: [] }, testsFamily()];

    const html = await render(HomePage, {});

    const legend = /<ul aria-label="Legend"[^>]*>([\s\S]*?)<\/ul>/.exec(html)?.[1] ?? '';
    expect(text(legend).trim()).toBe('Reference, 1,000 Unbounded');
  });
});

describe('family panel', () => {
  test("draws the latest round's intervals and names its leader", async () => {
    const html = await render(FamilyPanel, { family: await family() });

    expect(text(html)).toContain('Votes · Round 2026-10-21, 340 votes');
    expect(text(html)).toContain('Acme Pro 955–1,065 1,004');
    expect(html).toContain('aria-label="Leader"');
    expect(text(html)).toContain('Acme Large, tied with the top score, chosen by the lower cost');
  });

  test('keeps the unranked systems and those without votes after the round', async () => {
    const html = await render(FamilyPanel, { family: await family() });

    expect(text(html)).toContain('Acme Edge Unbounded, too few comparisons to place it unranked');
    expect(text(html)).toContain('Acme New Unbounded no votes');
  });

  test.each([
    ['with rounds', family],
    ['before its first round', async () => ({ ...(await family()), rounds: [] })],
    ['scored by tests', () => Promise.resolve(testsFamily())],
  ])('names the family and its systems by display name %s', async (_, load) => {
    const loaded = await load();

    const html = await render(FamilyPanel, { family: loaded });

    expect(text(html)).toContain(`${loaded.config.display_name} `);
    expect(shownSlugs(html, { ...SLUGS, smoke: 'Smoke Tasks' })).toEqual([]);
  });

  test("lists a tests family's systems by pass rate, best first", async () => {
    const html = await render(FamilyPanel, { family: testsFamily() });

    expect(text(html)).toContain('System Pass rate Acme Pro 75% Acme Small 25%');
  });
});

describe('system page', () => {
  test("summarizes the system's own standing in its latest round", async () => {
    const rounds = votesRounds(await family());

    const html = await render(SystemPage, systemProps('acme-pro', rounds));

    expect(text(html)).toContain(
      'Arena Writing Acme Pro Score 1,004 (955–1,065) Rank 1–3 Votes 114 (89 decisive) Round 2026-10-21',
    );
  });

  test('names the system, its family, and every other system by display name', async () => {
    const rounds = votesRounds(await family());

    const html = await render(SystemPage, systemProps('acme-pro', rounds));

    expect(html).toContain('<title>Acme Pro on Arena Writing | HAKULA†BENCH</title>');
    expect(shownSlugs(html, SLUGS)).toEqual([]);
  });

  test('names which output length the rater favors', async () => {
    const rounds = votesRounds(await family());

    const html = await render(SystemPage, systemProps('acme-pro', rounds));

    expect(text(html)).toContain(
      "Length preference Shorter, 6.5 points per SD of the pair's relative length difference",
    );
  });

  test('leaves out the head-to-head chart for a system without votes', async () => {
    const rounds = votesRounds(await family());

    const html = await render(SystemPage, systemProps('acme-tiny', rounds));

    expect(text(html)).toContain(
      'Acme Tiny Preliminary Score 233 (81–321) Rank 6 Votes 0 (0 decisive)',
    );
    expect(html).not.toContain('Head to head');
  });
});

describe('header', () => {
  test('links each listed family by its display name', async () => {
    listed.families = [await family()];

    const html = await render(MethodologyPage, {});

    const nav = /<nav\b[^>]*aria-label="Main"[^>]*>([\s\S]*?)<\/nav>/.exec(html)?.[1] ?? '';
    const links = [...nav.matchAll(/<li><a href="([^"]+)"[^>]*>([^<]*)<\/a>/g)].map(
      ([, href, label]) => [href, label.trim()],
    );

    expect(links).toEqual([
      ['/arena/', 'Arena Writing'],
      ['/methodology/', 'Methodology'],
    ]);
  });

  test('marks a link to an ancestor of the current page as current, not as the page', async () => {
    const html = await render(Header, {}, '/methodology/votes/');

    expect(html).toMatch(/<a href="\/methodology\/" aria-current="true"/);
    expect(html).not.toContain('aria-current="page"');
  });

  test('marks the link to the page itself as the current page', async () => {
    const html = await render(Header, {}, '/methodology/');

    expect(html).toMatch(/<a href="\/methodology\/" aria-current="page"/);
  });
});
