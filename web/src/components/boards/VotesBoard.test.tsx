import { renderToString } from 'react-dom/server';
import { afterEach, describe, expect, test, vi } from 'vitest';

import { VotesBoard } from '@/components/boards/VotesBoard';
import { reportSchema } from '@/lib/report';
import type { VotesRound } from '@/lib/reports';
import { readFixture } from '@/test/fixture';

const search = vi.hoisted(() => new URLSearchParams());
vi.mock('@/lib/use-search-param', () => ({
  useSearchParam: (name: string, fallback: string) => [search.get(name) ?? fallback, () => null],
}));

async function load(name: 'report' | 'report-early' = 'report'): Promise<VotesRound> {
  const report = reportSchema.parse(await readFixture(name));
  if (report.results.scorer !== 'votes') {
    throw new Error('the fixture is a votes report');
  }
  return { round: report.round, results: report.results };
}

function render({ round, results }: VotesRound): string {
  // The fixture lists standings by score, so reversing them leaves the board to sort.
  const reversed = { ...results, standings: results.standings.toReversed() };
  return renderToString(<VotesBoard family="arena" rounds={[{ round, results: reversed }]} />);
}

const text = (html: string) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');

describe('VotesBoard', () => {
  afterEach(() => {
    search.delete('category');
  });

  test('lists the ranked systems by score, best first', async () => {
    const html = render(await load());

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
    const html = render(await load());

    expect(text(html)).toContain(
      'Leader acme-large tied with the top score, chosen by the lower cost Votes 340 over 10 sessions, plus 20 left out of the fit',
    );
  });

  test('shows a dash for a system with no both-bad rate', async () => {
    const html = render(await load());

    expect(text(html)).toMatch(/acme-tiny Preliminary [^%]*? 0 – /);
  });

  test('names the unranked systems after the table', async () => {
    const html = render(await load());

    expect(text(html)).toContain(
      'Unranked until more comparisons reach the reference: acme-edge, acme-nano.',
    );
  });

  test('names no leader while a preliminary system ties with the top score', async () => {
    const html = render(await load('report-early'));

    expect(text(html)).toContain(
      'Leader – none while a preliminary system ties with the top score Votes 34 over 1 session, plus 2 left out of the fit',
    );
  });

  test('leaves out the cost chart when no system is priced', async () => {
    const round = await load();
    for (const standing of round.results.standings) {
      standing.mean_cost_usd = null;
    }

    const html = render(round);

    expect(html).not.toContain('Mean cost per output');
    expect(html).toContain('Median latency');
  });

  test('keeps every heatmap row and the systems only the category ranks', async () => {
    const round = await load();
    const message = round.results.categories.find((board) => board.category === 'message');
    const refit = message?.standings.find((item) => item.system === 'acme-mini');
    if (!message || !refit) {
      throw new Error('the fixture refits acme-mini in message');
    }
    refit.system = 'acme-edge';
    message.unranked = ['acme-nano'];
    search.set('category', 'message');

    const html = render(round);

    expect(text(html)).toContain('acme-edge Preliminary 890 798–1,044 41 – – – –');
    expect(html).not.toContain('href="/arena/acme-edge/"');
    expect(text(html)).toContain('acme-mini 769 (696–867), 38 votes, preliminary – 789');
    expect(text(html)).toContain('acme-edge – 890 (798–1,044), 41 votes, preliminary – Score');
  });
});
