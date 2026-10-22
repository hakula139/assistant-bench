import { getContainerRenderer } from '@astrojs/react/container-renderer';
import { experimental_AstroContainer as AstroContainer } from 'astro/container';
import { loadRenderers } from 'astro:container';
import { describe, expect, test } from 'vitest';

import type { FamilyConfig } from '@/lib/family';
import { reportSchema } from '@/lib/report';
import { votesRounds, type Family } from '@/lib/reports';
import FamilyPage from '@/pages/[family]/index.astro';
import SystemPage from '@/pages/[family]/[system].astro';
import MethodologyPage from '@/pages/methodology.astro';
import { readFixture } from '@/test/fixture';

const arenaConfig: FamilyConfig = {
  scorer: 'votes',
  systems: ['acme-large', 'acme-pro', 'acme-small'],
  reference: 'acme-large',
  categories: ['docs', 'message', 'reply'],
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
    config: { scorer: 'tests', systems: ['acme-pro'], categories: [], public: true },
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

    expect(text(html)).toContain('No round has closed yet. These 3 systems are in the first one.');
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
