import { getContainerRenderer } from '@astrojs/react/container-renderer';
import { experimental_AstroContainer as AstroContainer } from 'astro/container';
import { loadRenderers } from 'astro:container';
import { describe, expect, test } from 'vitest';

import { reportSchema } from '@/lib/report';
import { votesRounds, type Family } from '@/lib/reports';
import FamilyPage from '@/pages/[family]/index.astro';
import SystemPage from '@/pages/[family]/[system].astro';
import { readFixture } from '@/test/fixture';

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
  return { name: 'arena', rounds: [earlier, latest] };
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

describe('family page', () => {
  test('renders the votes board with the round summary', async () => {
    const html = await render(FamilyPage, { family: await family() });

    expect(text(html)).toContain('Leader acme-large tied at rank 1, chosen by the lower cost');
  });
});

describe('system page', () => {
  test("summarizes the system's own standing in its latest round", async () => {
    const rounds = votesRounds(await family());

    const html = await render(SystemPage, { family: 'arena', system: 'acme-pro', rounds });

    expect(text(html)).toContain(
      'acme-pro Score 965 (915–1,017) Rank 1–5 Votes 122 (96 decisive) Round 2026-10-21',
    );
  });
});
