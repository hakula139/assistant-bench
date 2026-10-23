// @vitest-environment jsdom
import { cleanup, fireEvent, render as renderInDom, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderToString } from 'react-dom/server';
import { afterEach, describe, expect, test, vi } from 'vitest';

import { VotesBoard } from '@/components/boards/VotesBoard';
import { boardLabels } from '@/lib/method';
import { reportSchema } from '@/lib/report';
import type { VotesRound } from '@/lib/reports';
import { FIXTURE_NAMES, readFixture, shownSlugs } from '@/test/fixture';
import { stubPlotFrame } from '@/test/plot';

const search = vi.hoisted(() => new URLSearchParams());
vi.mock('@/lib/use-search-param', () => ({
  useSearchParam: (name: string, fallback: string) => [
    search.get(name) ?? fallback,
    (value: string) => {
      search.set(name, value);
    },
  ],
}));

async function load(name: 'report' | 'report-early' = 'report'): Promise<VotesRound> {
  const report = reportSchema.parse(await readFixture(name));
  if (report.results.scorer !== 'votes') {
    throw new Error('the fixture is a votes report');
  }
  return { round: report.round, results: report.results };
}

const BOARD = '/writing/arena/';

const CASES = boardLabels({ render: 'text' });

function render({ round, results }: VotesRound, names = FIXTURE_NAMES, labels = CASES): string {
  // The fixture lists standings by score, so reversing them leaves the board to sort.
  const reversed = { ...results, standings: results.standings.toReversed() };
  return renderToString(
    <VotesBoard
      base={BOARD}
      rounds={[{ round, results: reversed }]}
      names={names}
      labels={labels}
    />,
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

const parse = (html: string) => new DOMParser().parseFromString(html, 'text/html');

/** The round summary's figures as `[label, value]`, read as the page shows them. */
const stats = (html: string) =>
  [...parse(html).querySelectorAll('dl > div')].map((stat) => [
    stat.querySelector('dt')?.textContent,
    stat.querySelector('dd')?.textContent,
  ]);

/** The text of each hint trigger in the round summary. */
const statHints = (html: string) =>
  [...parse(html).querySelectorAll('dl [aria-haspopup="dialog"]')].map((hint) => hint.textContent);

const legend = (html: string) =>
  text(/<ul aria-label="Legend"[^>]*>([\s\S]*?)<\/ul>/.exec(html)?.[1] ?? '').trim();

describe('VotesBoard', () => {
  afterEach(() => {
    search.delete('category');
    search.delete('round');
    cleanup();
  });

  test('lists the ranked systems by score, best first', async () => {
    const html = render(await load());

    const systems = [...html.matchAll(/href="\/writing\/arena\/([^/]+)\/"/g)].map(
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

  test('names the leader and the votes left out, with hints on the tie and the left-out votes', async () => {
    const html = render(await load());

    expect(stats(html)).toEqual([
      ['Leader', 'Acme Large, tied'],
      ['Votes', '340 over 10 sessions, 20 left out'],
      ['Rater agreement', '83% on 23 repeats'],
      ['Separated pairs', '67%'],
      ['Split-half τ', '0.60'],
      ['Length bias', 'Favors shorter, 6.5'],
    ]);
    expect(statHints(html)).toEqual([
      'Leader',
      'tied',
      '20 left out',
      'Rater agreement',
      'Separated pairs',
      'Split-half τ',
      'Length bias',
    ]);
  });

  test.each([
    ['text', 'Length bias', 'Gate failures', 'output'],
    ['diff', 'Diff length bias', 'Failed tests', 'solution'],
  ] as const)(
    'labels the length bias, the gate failures, and the cost of a %s family',
    async (output, lengthBias, gateFailures, unit) => {
      const html = render(await load(), FIXTURE_NAMES, boardLabels({ render: output }));

      expect(stats(html).at(-1)?.[0]).toBe(lengthBias);
      expect(text(head(html))).toContain(`${gateFailures} What ${gateFailures} means`);
      expect(text(html)).toContain(`Mean cost per ${unit}`);
      expect(text(html)).toContain(`Cost / ${unit}`);
    },
  );

  test('leaves out the left-out count and the length bias when the round has neither', async () => {
    const round = await load();
    round.results.dropped_votes = 0;
    round.results.length = null;

    const html = render(round);

    expect(stats(html).map(([label]) => label)).not.toContain('Length bias');
    expect(stats(html)[1]).toEqual(['Votes', '340 over 10 sessions']);
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

    expect(legend(html)).toBe('Score, 95% interval Interval past the axis Anchor, 1,000');
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
    renderInDom(
      <VotesBoard
        base={BOARD}
        rounds={[{ round, results }]}
        names={FIXTURE_NAMES}
        labels={CASES}
      />,
    );

    await userEvent.setup().click(screen.getByRole('button', { name: 'Anchor' }));

    expect((await screen.findByRole('dialog', { name: 'Anchor' })).textContent).toContain(
      'The most-voted system, Acme Large, fixed at 1,000 in place of the reference Acme New, which that round could not rank.',
    );
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

    expect(parse(html).body.textContent).toContain(
      'Agreement below 70%, so the intervals overstate separation.',
    );
  });

  test('names no leader while a preliminary system ties with the top score', async () => {
    const html = render(await load('report-early'));

    expect(stats(html).slice(0, 2)).toEqual([
      ['Leader', 'No leader yet'],
      ['Votes', '34 over 1 session, 2 left out'],
    ]);
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
      ' System 95% interval Score Votes What Votes means Both bad all categories What Both bad means Gate failures all categories What Gate failures means ',
    );
  });

  test('shows the family board for a category the round does not have', async () => {
    search.set('category', 'missing');

    const html = render(await load());

    expect(text(head(html))).toMatch(/^ Rank What Rank means System /);
    expect(text(html)).toContain('1–3 Acme Pro 1,004 955–1,065 114');
  });

  test('writes the category picked to the address', async () => {
    const { round, results } = await load();
    stubPlotFrame();
    renderInDom(
      <VotesBoard
        base={BOARD}
        rounds={[{ round, results }]}
        names={FIXTURE_NAMES}
        labels={CASES}
      />,
    );

    fireEvent.change(screen.getByLabelText('Category'), { target: { value: 'message' } });

    expect(search.get('category')).toBe('message');
  });

  test.each([
    ['95% interval', 'max-lg:hidden'],
    ['Votes', 'max-md:hidden'],
    ['Both bad', 'max-md:hidden'],
    ['Gate failures', 'max-md:hidden'],
    ['Score', null],
  ])('hides the %s column on narrow screens with %s', async (name, hidden) => {
    const leaderboard = parse(render(await load())).querySelector('table');

    const columns = [...(leaderboard?.querySelectorAll('thead th') ?? [])].map((cell) =>
      text(cell.innerHTML).trim(),
    );
    const index = columns.findIndex((column) => column.startsWith(name));
    const rules = [...(leaderboard?.querySelectorAll('tr') ?? [])].map(
      (row) =>
        row.children
          .item(index)
          ?.className.split(' ')
          .find((rule) => rule.endsWith(':hidden')) ?? null,
    );

    expect(index, name).toBeGreaterThan(-1);
    expect(new Set(rules), name).toEqual(new Set([hidden]));
  });

  test('shows the category legend where the leaderboard legend hides with the interval column', async () => {
    const document = parse(render(await load()));

    const legends = [...document.querySelectorAll('ul[aria-label="Legend"]')].map((list) => [
      list.className.split(' ').find((rule) => rule.endsWith(':hidden')),
      list.closest('section')?.querySelector('h2')?.textContent ?? null,
      text(list.innerHTML).trim(),
    ]);

    expect(legends).toEqual([
      ['max-lg:hidden', null, legend(render(await load()))],
      ['lg:hidden', 'Categories', expect.stringContaining('Preliminary')],
    ]);
  });

  test.each([
    ['all', '1,004', 'text', '114 votes, both bad 5%, gate failures 2%.'],
    ['message', '972', 'text', '34 votes, both bad 5%, gate failures 2% across all categories.'],
    ['all', '1,004', 'diff', '114 votes, both bad 5%, failed tests 2%.'],
  ] as const)(
    "opens a system's votes and rates from its score on the %s board of a %s family",
    async (category, score, render, details) => {
      search.set('category', category);
      const { round, results } = await load();
      stubPlotFrame();
      renderInDom(
        <VotesBoard
          base={BOARD}
          rounds={[{ round, results }]}
          names={FIXTURE_NAMES}
          labels={boardLabels({ render })}
        />,
      );

      await userEvent.setup().click(screen.getByRole('button', { name: score }));

      expect((await screen.findByRole('dialog', { name: 'Acme Pro' })).textContent).toContain(
        details,
      );
    },
  );

  test("draws the anchor's score as one rule through every row", async () => {
    const { round, results } = await load();
    results.reference = 'acme-new';
    stubPlotFrame();

    const { container } = renderInDom(
      <VotesBoard
        base={BOARD}
        rounds={[{ round, results }]}
        names={FIXTURE_NAMES}
        labels={CASES}
      />,
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
    expect(rows.map((row) => row.querySelector('svg.bench-plot')?.getAttribute('height'))).toEqual(
      rows.map((row) =>
        row.querySelector('svg.bench-plot')?.parentElement?.style.minHeight.replace('px', ''),
      ),
    );
  });

  test('draws no anchor rule in a category that leaves the anchor out', async () => {
    const { round, results } = await load();
    const message = results.categories.find((board) => board.category === 'message');
    if (!message) {
      throw new Error('the fixture has a message category');
    }
    message.standings = message.standings.filter((refit) => refit.system !== results.anchor);
    search.set('category', 'message');
    stubPlotFrame();

    const { container } = renderInDom(
      <VotesBoard
        base={BOARD}
        rounds={[{ round, results }]}
        names={FIXTURE_NAMES}
        labels={CASES}
      />,
    );
    const rows = await vi.waitFor(() => {
      const drawn = [...container.querySelectorAll('tbody tr')].filter((row) =>
        row.querySelector('svg'),
      );
      expect(drawn).toHaveLength(message.standings.length);
      return drawn;
    });

    expect(rows.map(verticalRules)).toEqual(rows.map(() => []));
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

    expect(text(html)).toContain('Acme Edge Preliminary 890 798–1,044 41 – –');
    expect(html).not.toContain(`href="${BOARD}acme-edge/"`);
    expect(text(html)).toContain('Acme Mini 769 (696–867), 38 votes, preliminary – 789');
    expect(text(html)).toContain('Acme Edge – 890 (798–1,044), 41 votes, preliminary – Score');
  });
});
