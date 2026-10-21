import { renderToString } from 'react-dom/server';
import { describe, expect, test } from 'vitest';

import { VotesBoard } from '@/components/boards/VotesBoard';
import { reportSchema } from '@/lib/report';
import { readFixture } from '@/test/fixture';

async function render(): Promise<string> {
  const report = reportSchema.parse(await readFixture());
  if (report.results.scorer !== 'votes') {
    throw new Error('the fixture is a votes report');
  }
  // The fixture lists standings by score, so reversing them leaves the board to sort.
  const results = { ...report.results, standings: report.results.standings.toReversed() };
  return renderToString(<VotesBoard family="arena" rounds={[{ round: report.round, results }]} />);
}

describe('VotesBoard', () => {
  test('lists the systems by score, best first', async () => {
    const html = await render();

    const systems = [...html.matchAll(/href="\/arena\/([^/]+)\/"/g)].map((match) => match[1]);

    expect(systems).toEqual(['acme-large', 'acme-pro', 'acme-small', 'acme-flash', 'acme-mini']);
  });

  test('names the leader and how its tie at rank 1 was broken', async () => {
    const html = await render();

    expect(html).toMatch(
      /Leader<\/dt><dd[^>]*>acme-large<span[^>]*>tied at rank 1, chosen by the lower cost</,
    );
  });
});
