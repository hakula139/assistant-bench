import { renderToString } from 'react-dom/server';
import { describe, expect, test } from 'vitest';

import { VotesBoard } from '@/components/boards/VotesBoard';
import { reportSchema, type VotesResults } from '@/lib/report';
import { readFixture } from '@/test/fixture';

/** Renders the fixture's board, with `change` applied to its results. */
async function render(change: Partial<VotesResults> = {}): Promise<string> {
  const report = reportSchema.parse(await readFixture());
  if (report.results.scorer !== 'votes') {
    throw new Error('the fixture is a votes report');
  }
  // The fixture lists standings by score, so reversing them leaves the board to sort.
  const results = {
    ...report.results,
    standings: report.results.standings.toReversed(),
    ...change,
  };
  return renderToString(<VotesBoard family="arena" rounds={[{ round: report.round, results }]} />);
}

const text = (html: string) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');

describe('VotesBoard', () => {
  test('lists the systems by score, best first', async () => {
    const html = await render();

    const systems = [...html.matchAll(/href="\/arena\/([^/]+)\/"/g)].map((match) => match[1]);

    expect(systems).toEqual(['acme-large', 'acme-pro', 'acme-small', 'acme-flash', 'acme-mini']);
  });

  test('names the leader and how its tie with the top score was broken', async () => {
    const html = await render();

    expect(text(html)).toContain(
      'Leader acme-large tied with the top score, chosen by the lower cost',
    );
  });

  test('names no leader while every system is preliminary', async () => {
    const html = await render({ decision: null });

    expect(text(html)).toContain('Leader – none while every system is preliminary');
  });

  test('names the unranked systems after the table', async () => {
    const html = await render({ unranked: ['acme-new', 'acme-old'] });

    expect(text(html)).toContain(
      'Unranked: acme-new, acme-old. No comparison connects them to the reference system',
    );
  });
});
