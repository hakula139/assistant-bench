// @vitest-environment jsdom
import { cleanup, render as renderInDom, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderToString } from 'react-dom/server';
import { afterEach, describe, expect, test, vi } from 'vitest';

import { VotesBoard } from '@/components/boards/VotesBoard';
import { reportSchema } from '@/lib/report';
import type { VotesRound } from '@/lib/reports';
import { FIXTURE_NAMES, readFixture, shownSlugs } from '@/test/fixture';
import { stubPlotFrame } from '@/test/plot';

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

const BOARD = '/writing/chinese/';

function render({ round, results }: VotesRound, names = FIXTURE_NAMES): string {
  // The fixture lists standings by score, so reversing them leaves the board to sort.
  const reversed = { ...results, standings: results.standings.toReversed() };
  return renderToString(
    <VotesBoard base={BOARD} rounds={[{ round, results: reversed }]} names={names} />,
  );
}

const text = (html: string) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');

const head = (html: string) => html.slice(html.indexOf('<thead'), html.indexOf('</thead>'));

/** The x positions of a row's vertical rules, which only the anchor draws. */
function verticalRules(row: Element): number[] {
  return [...row.querySelectorAll('g[aria-label="rule"] line')]
    .filter((line) => line.getAttribute('y1') !== line.getAttribute('y2'))
    .map((line) => Number(line.getAttribute('x1')));
}

const legend = (html: string) =>
  text(/<ul aria-label="Legend"[^>]*>([\s\S]*?)<\/ul>/.exec(html)?.[1] ?? '').trim();

describe('VotesBoard', () => {
  afterEach(() => {
    search.delete('category');
    cleanup();
  });

  test('lists the ranked systems by score, best first', async () => {
    const html = render(await load());

    const systems = [...html.matchAll(/href="\/writing\/chinese\/([^/]+)\/"/g)].map(
      (match) => match[1],
    );

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
      'Leader Acme Large tied with the top score, chosen by the lower cost Votes 340 over 10 sessions, plus 20 left out of the fit',
    );
  });

  test('shows a dash for a system with no both-bad rate', async () => {
    const html = render(await load());

    expect(text(html)).toMatch(/Acme Tiny Preliminary [^%]*? 0 – /);
  });

  test('spells out the preliminary mark and leaves the anchor mark an icon', async () => {
    const html = render(await load());

    expect(html).toMatch(/<span>Preliminary<\/span>/);
    expect(html).toMatch(/<span class="sr-only">Anchor<\/span>/);
  });

  test('keys the legend to the marks the board draws', async () => {
    const round = await load();
    for (const standing of round.results.standings) {
      standing.preliminary = false;
    }

    const html = render(round);

    expect(legend(html)).toBe('Score, 95% interval Past the axis Anchor, 1,000');
  });

  test('marks only the anchor and the preliminary systems', async () => {
    const html = render(await load());

    const marked = (status: string) =>
      [...text(html).matchAll(new RegExp(`(Acme [A-Z][a-z]+) ${status} `, 'g'))].map((m) => m[1]);

    expect(marked('Anchor')).toEqual(['Acme Large']);
    expect(marked('Preliminary')).toEqual(['Acme Tiny']);
  });

  test('marks the stand-in anchor when the reference is not ranked', async () => {
    const round = await load();
    round.results.reference = 'acme-new';

    const html = render(round);

    expect(html).toMatch(/<span class="sr-only">Anchor<\/span>/);
    expect(text(html)).toMatch(/Acme Large Anchor/);
    expect(legend(html)).toContain('Anchor, 1,000');
  });

  test('names the reference the stand-in anchor replaces', async () => {
    const { round, results } = await load();
    results.reference = 'acme-new';
    stubPlotFrame();
    renderInDom(<VotesBoard base={BOARD} rounds={[{ round, results }]} names={FIXTURE_NAMES} />);

    await userEvent.setup().click(screen.getByRole('button', { name: 'Anchor' }));

    expect(await screen.findByText(/reference Acme New, which is not ranked yet/)).toBeTruthy();
  });

  test('names the unranked systems after the table, sorted by display name', async () => {
    const html = render(await load(), { ...FIXTURE_NAMES, 'acme-edge': 'Acme Zeta' });

    expect(text(html)).toContain(
      'Unranked with too few comparisons to place them: Acme Nano, Acme Zeta',
    );
  });

  test("labels each system's interval with its display name and score", async () => {
    const html = render(await load());

    expect(html).toContain('aria-label="Acme Pro: 1,004, 95% interval 955–1,065"');
  });

  test.each(['all', 'message'])(
    'shows display names wherever the %s board names a system',
    async (category) => {
      const round = await load();
      round.results.standings[0].mean_cost_usd = null;
      search.set('category', category);

      const html = render(round);

      expect(text(html)).toContain(`leaving out the unpriced ${FIXTURE_NAMES['acme-pro']}`);
      expect(shownSlugs(html)).toEqual([]);
    },
  );

  test('warns when rater agreement falls below the floor', async () => {
    const round = await load();
    round.results.rater_agreement.below_floor = true;

    const html = render(round);

    expect(text(html)).toContain('Agreement below 70%, so the intervals overstate separation.');
  });

  test('names no leader while a preliminary system ties with the top score', async () => {
    const html = render(await load('report-early'));

    expect(text(html)).toContain(
      'Leader – none until every system tied for the top has 40 decisive votes Votes 34 over 1 session, plus 2 left out of the fit',
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

  test('drops the rank column in a category and marks the columns that stay family-wide', async () => {
    search.set('category', 'message');

    const html = render(await load());

    expect(text(head(html))).toBe(
      ' System 95% interval Score Votes Both bad all categories Gate failures all categories What Gate failures means Cost / output all categories p50 latency all categories ',
    );
  });

  test('shows the family board for a category the round does not have', async () => {
    search.set('category', 'missing');

    const html = render(await load());

    expect(text(head(html))).toMatch(/^ Rank System /);
    expect(text(html)).toContain('1–3 Acme Pro 1,004 955–1,065 114');
  });

  test("draws the anchor's score as one rule through every row", async () => {
    const { round, results } = await load();
    results.reference = 'acme-new';
    stubPlotFrame();

    const { container } = renderInDom(
      <VotesBoard base={BOARD} rounds={[{ round, results }]} names={FIXTURE_NAMES} />,
    );
    const rows = await vi.waitFor(() => {
      const drawn = [...container.querySelectorAll('tbody tr')].filter((row) =>
        row.querySelector('svg'),
      );
      expect(drawn).toHaveLength(results.standings.length);
      return drawn;
    });

    const anchorRow = rows.find((row) => row.querySelector(`a[href="${BOARD}${results.anchor}/"]`));
    const anchorScore = Number(
      anchorRow?.querySelector('g[aria-label="dot"] circle')?.getAttribute('cx'),
    );

    expect(rows.map(verticalRules)).toEqual(rows.map(() => [anchorScore]));
  });

  test('keeps every category row and the systems only the category ranks', async () => {
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

    expect(text(html)).toContain('Acme Edge Preliminary 890 798–1,044 41 – – – –');
    expect(html).not.toContain(`href="${BOARD}acme-edge/"`);
    expect(text(html)).toContain('Acme Mini 769 (696–867), 38 votes, preliminary – 789');
    expect(text(html)).toContain('Acme Edge – 890 (798–1,044), 41 votes, preliminary – Score');
  });
});
