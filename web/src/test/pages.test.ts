import { getContainerRenderer } from '@astrojs/react/container-renderer';
import { experimental_AstroContainer as AstroContainer } from 'astro/container';
import { loadRenderers } from 'astro:container';
import { describe, expect, test } from 'vitest';

import FamilyPanel from '@/components/site/FamilyPanel.astro';
import Forest from '@/components/site/Forest.astro';
import type { FamilyConfig } from '@/lib/family';
import { reportSchema } from '@/lib/report';
import { votesRounds, type Family } from '@/lib/reports';
import FamilyPage from '@/pages/[family]/index.astro';
import SystemPage from '@/pages/[family]/[system].astro';
import MethodologyPage from '@/pages/methodology.astro';
import { readFixture } from '@/test/fixture';

const arenaConfig: FamilyConfig = {
  scorer: 'votes',
  systems: ['acme-large', 'acme-new', 'acme-pro', 'acme-small'],
  reference: 'acme-large',
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
  return { name: 'arena', config: arenaConfig, rounds: [earlier, latest] };
}

async function render(
  page: Parameters<AstroContainer['renderToString']>[0],
  props: Record<string, unknown>,
): Promise<string> {
  const container = await AstroContainer.create({
    renderers: await loadRenderers([getContainerRenderer()]),
  });
  return container.renderToString(page, { props });
}

const text = (html: string) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');

/** A family scored by tests, with one round. */
function testsFamily(): Family {
  const report = reportSchema.parse({
    family: 'smoke',
    round: '2026-10-21',
    closed_at: '2026-10-21T18:00:00Z',
    results: {
      scorer: 'tests',
      standings: [
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
    config: { scorer: 'tests', systems: ['acme-pro'], public: true },
    rounds: [report],
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
  test('renders the votes board with the round summary', async () => {
    const html = await render(FamilyPage, { family: await family() });

    expect(text(html)).toContain(
      'Ranked by blind pairwise votes, with 95% intervals and acme-large as the reference at 1,000.',
    );
    expect(text(html)).toContain(
      'Leader acme-large tied with the top score, chosen by the lower cost',
    );
  });

  test('lists the systems with unbounded intervals before the first round closes', async () => {
    const empty: Family = { name: 'arena', config: arenaConfig, rounds: [] };

    const html = await render(FamilyPage, { family: empty });

    expect(text(html)).toContain('No round has closed yet, and the first one holds 4 systems.');
    expect(text(html)).toContain('acme-large Fixed at 1,000 as the reference 1,000');
    expect(text(html)).toContain('acme-pro Unbounded no votes');
    expect(html).not.toContain('Leader');
  });

  test.each([
    ['votes', family],
    ['tests', () => Promise.resolve(testsFamily())],
  ])('links the %s board to a methodology section that exists', async (_, load) => {
    const methodology = await render(MethodologyPage, {});

    const html = await render(FamilyPage, { family: await load() });

    expect(methodology).toContain(`id="${guide(html)}"`);
  });
});

describe('family panel', () => {
  test("draws the latest round's intervals and names its leader", async () => {
    const html = await render(FamilyPanel, { family: await family() });

    expect(text(html)).toContain('Round 2026-10-21, 340 votes');
    expect(text(html)).toContain('acme-pro 955–1,065 1,004');
    expect(text(html)).toContain(
      'Leader: acme-large, tied with the top score, chosen by the lower cost',
    );
  });

  test('keeps the unranked systems and those without votes after the round', async () => {
    const html = await render(FamilyPanel, { family: await family() });

    expect(text(html)).toContain('acme-edge Unbounded, too few comparisons to place it unranked');
    expect(text(html)).toContain('acme-new Unbounded no votes');
  });
});

describe('static forest', () => {
  /** Each body row's markup, by system. */
  async function rows(): Promise<Record<string, string>> {
    const estimate = (score: number, lower: number, upper: number) => ({
      score,
      interval: { lower, upper },
    });
    const html = await render(Forest, {
      caption: 'Example',
      domain: [400, 1100],
      rows: [
        { system: 'settled', estimate: estimate(1000, 950, 1050), preliminary: false },
        { system: 'preliminary', estimate: estimate(900, 800, 1000), preliminary: true },
        { system: 'cut', estimate: estimate(500, 300, 700), preliminary: false },
      ],
    });
    const body = html.slice(html.indexOf('<tbody'));
    return Object.fromEntries(
      body
        .split('<tr')
        .slice(1)
        .map((row): [string, string] => [/scope="row"[^>]*>([^<]+)</.exec(row)?.[1] ?? '', row]),
    );
  }

  test('dashes only the preliminary interval', async () => {
    const { settled, preliminary } = await rows();

    expect(settled).toContain('border-solid');
    expect(settled).not.toContain('border-dashed');
    expect(preliminary).toContain('border-dashed');
  });

  test('ends an interval the axis cuts in an arrowhead', async () => {
    const { settled, cut } = await rows();

    expect(cut).toContain('<svg');
    expect(settled).not.toContain('<svg');
  });
});

describe('system page', () => {
  test("summarizes the system's own standing in its latest round", async () => {
    const rounds = votesRounds(await family());

    const html = await render(SystemPage, { family: 'arena', system: 'acme-pro', rounds });

    expect(text(html)).toContain(
      'acme-pro Score 1,004 (955–1,065) Rank 1–3 Votes 114 (89 decisive) Round 2026-10-21',
    );
  });

  test('names which output length the rater favors', async () => {
    const rounds = votesRounds(await family());

    const html = await render(SystemPage, { family: 'arena', system: 'acme-pro', rounds });

    expect(text(html)).toContain(
      'the rater favors the shorter output by 6.5 score points per standard deviation',
    );
  });

  test('leaves out the head-to-head chart for a system without votes', async () => {
    const rounds = votesRounds(await family());

    const html = await render(SystemPage, { family: 'arena', system: 'acme-tiny', rounds });

    expect(text(html)).toContain('acme-tiny Score 233 (81–321) Rank 6 Votes 0 (0 decisive)');
    expect(html).not.toContain('Head to head');
  });
});
