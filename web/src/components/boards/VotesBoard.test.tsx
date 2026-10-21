import { renderToString } from 'react-dom/server';
import { describe, expect, test } from 'vitest';

import { VotesBoard } from '@/components/boards/VotesBoard';
import { reportSchema } from '@/lib/report';
import { readFixture } from '@/test/fixture';

async function render(name: 'report' | 'report-early' = 'report'): Promise<string> {
  const report = reportSchema.parse(await readFixture(name));
  if (report.results.scorer !== 'votes') {
    throw new Error('the fixture is a votes report');
  }
  // The fixture lists standings by score, so reversing them leaves the board to sort.
  const results = { ...report.results, standings: report.results.standings.toReversed() };
  return renderToString(<VotesBoard family="arena" rounds={[{ round: report.round, results }]} />);
}

const text = (html: string) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');

describe('VotesBoard', () => {
  test('lists the ranked systems by score, best first', async () => {
    const html = await render();

    const systems = [...html.matchAll(/href="\/arena\/([^/]+)\/"/g)].map((match) => match[1]);

    expect(systems).toEqual([
      'acme-pro',
      'acme-large',
      'acme-small',
      'acme-flash',
      'acme-mini',
      'acme-tiny',
    ]);
  });

  test('names the leader, how its tie was broken, and the votes left out', async () => {
    const html = await render();

    expect(text(html)).toContain(
      'Leader acme-large tied with the top score, chosen by the lower cost Votes 340 over 10 sessions, 20 more left out',
    );
  });

  test('shows a dash for a system with no both-bad rate', async () => {
    const html = await render();

    expect(text(html)).toMatch(/acme-tiny Preliminary [^%]*? 0 – /);
  });

  test('names the unranked systems after the table', async () => {
    const html = await render();

    expect(text(html)).toContain(
      'Unranked: acme-edge, acme-nano. Too few comparisons connect them to the reference system',
    );
  });

  test('names no leader while a preliminary system ties with the top score', async () => {
    const html = await render('report-early');

    expect(text(html)).toContain(
      'Leader – none while a preliminary system ties with the top score',
    );
  });
});
