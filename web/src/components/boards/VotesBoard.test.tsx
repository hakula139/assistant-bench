// @vitest-environment jsdom
import { cleanup, render as renderInDom, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderToString } from 'react-dom/server';
import { afterEach, describe, expect, test, vi } from 'vitest';

import { VotesBoard } from '@/components/boards/VotesBoard';
import { Forest } from '@/components/site/Forest';
import { forestDomain, roundRows } from '@/lib/forest';
import { boardLabels } from '@/lib/method';
import { reportSchema } from '@/lib/report';
import type { VotesRound } from '@/lib/reports';
import { reportNames } from '@/lib/systems';
import { FIXTURE_NAMES, readFixture, shownSlugs } from '@/test/fixture';
import { stubPlotFrame } from '@/test/plot';
import { trackDrawing, type TrackDrawing } from '@/test/track';

const search = vi.hoisted(() => new URLSearchParams());
vi.mock('@/lib/use-search-param', () => ({
  useSearchParam: (name: string, fallback: string | null) => [
    search.get(name) ?? fallback,
    (value: string | null) => {
      if (value === null) {
        search.delete(name);
      } else {
        search.set(name, value);
      }
    },
  ],
}));

async function load(
  name: 'report' | 'report-early' | 'report-ledger' = 'report',
): Promise<VotesRound> {
  const report = reportSchema.parse(await readFixture(name));
  if (report.results.scorer !== 'votes') {
    throw new Error('the fixture is a votes report');
  }
  return {
    round: report.round,
    closed_at: report.closed_at,
    systems: report.systems,
    results: report.results,
  };
}

const BOARD = '/writing/arena/';

const CASES = boardLabels({ render: 'text' });
const CATEGORIES = ['docs', 'message', 'reply'] as const;

function render(
  round: VotesRound,
  names?: Record<string, string>,
  labels = CASES,
  categories: Category[] = [...CATEGORIES],
): string {
  const reversed = { ...round.results, standings: round.results.standings.toReversed() };
  const systems = names
    ? Object.fromEntries(
        Object.entries(names).map(([id, display_name]) => [
          id,
          { display_name, fingerprint: null },
        ]),
      )
    : round.systems;
  return renderToString(
    <VotesBoard
      base={BOARD}
      round={{ ...round, systems, results: reversed }}
      categories={categories}
      labels={labels}
    />,
  );
}

const text = (html: string) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');

const head = (html: string) => html.slice(html.indexOf('<thead'), html.indexOf('</thead>'));

const parse = (html: string) => new DOMParser().parseFromString(html, 'text/html');

/** Each drawn row's score track, keyed by the system name it links. */
const tracks = (html: string): Record<string, TrackDrawing> =>
  Object.fromEntries(
    [...parse(html).querySelectorAll('tbody tr')]
      .filter((row) => row.querySelector('[data-slot="score-track"]'))
      .map((row) => [row.querySelector('a')?.textContent ?? '', trackDrawing(row)]),
  );

/** The round summary's figures as `[label, value]`, read as the page shows them. */
const stats = (html: string) =>
  [...parse(html).querySelectorAll('dl > div')].map((stat) => [
    stat.querySelector('dt')?.textContent,
    stat.querySelector('dd')?.textContent,
  ]);

const statHints = (html: string) =>
  [...parse(html).querySelectorAll('dl [aria-haspopup="dialog"]')].map((hint) => hint.textContent);

const legend = (html: string) =>
  text(/<ul aria-label="Legend"[^>]*>([\s\S]*?)<\/ul>/.exec(html)?.[1] ?? '').trim();

describe('VotesBoard', () => {
  afterEach(() => {
    search.delete('category');
    cleanup();
  });

  test('separates genuine raw coverage from accepted choices and explains weighted rates', async () => {
    const round = await load('report-ledger');

    const html = render(round);

    expect(stats(html)).toEqual(
      expect.arrayContaining([
        ['Votes', '150 over 3 sessions, 1 left out'],
        ['Accepted choices', '151 including repeats and choices excluded from this fit'],
        ['Effective vote mass', '120 after the complete family actor cap'],
      ]),
    );
    expect(text(html)).toContain(
      'Scores, win rates and both-bad rates use the recorded actor weights.',
    );
    expect(text(html)).toContain('Repeats contribute only to consistency diagnostics.');
    expect(text(html)).not.toMatch(/refresh deadline|confidence interval/i);
  });

  test('does not query live freshness for a historical unit report', async () => {
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    stubPlotFrame();

    renderInDom(
      <VotesBoard base={BOARD} round={await load()} categories={[...CATEGORIES]} labels={CASES} />,
    );

    expect(screen.queryByRole('status')).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  test('draws the labels, coverage marks, and copy of the round it shows', async () => {
    const earlier = await load('report-early');
    const current = await load();
    earlier.systems['acme-pro'].display_name = 'Historical Pro';
    current.systems['acme-pro'].display_name = 'Current Pro';
    earlier.results.rules.preliminary_decisive_votes = 12;
    earlier.results.rules.rater_agreement_floor = 0.5;
    earlier.results.method.anchor_score = 1234;
    for (const row of earlier.results.standings) {
      row.score += 234;
    }
    for (const board of earlier.results.categories) {
      for (const row of board.standings) {
        row.score += 234;
      }
    }
    for (const round of [earlier, current]) {
      const pro = round.results.standings.find((row) => row.system === 'acme-pro');
      if (pro === undefined) {
        throw new Error('Fixture ranks acme-pro');
      }
      pro.decisive_votes = 13;
    }
    const draw = (round: VotesRound) =>
      renderToString(
        <VotesBoard base={BOARD} round={round} categories={[...CATEGORIES]} labels={CASES} />,
      );

    const historical = draw(earlier);

    const historicalRow = [...parse(historical).querySelectorAll('tbody tr')].find((row) =>
      row.querySelector(`a[href="${BOARD}acme-pro/"]`),
    );
    expect(historicalRow?.textContent).toContain('Historical Pro');
    expect(historicalRow?.textContent).not.toContain('Preliminary');
    expect(text(historical)).not.toContain('Current Pro');
    expect(text(historical)).toContain('anchor 1234');
    expect(text(historical)).toContain('12 raw decisive votes');
    expect(text(historical)).toContain('floor is 0.5');

    const latest = draw(current);

    const currentRow = [...parse(latest).querySelectorAll('tbody tr')].find((row) =>
      row.querySelector(`a[href="${BOARD}acme-pro/"]`),
    );
    expect(currentRow?.textContent).toContain('Current ProPreliminary');
    expect(text(latest)).not.toContain('Historical Pro');
    expect(text(latest)).toContain('40 raw decisive votes');
  });

  test('starts in score order and sorts systems in both directions', async () => {
    const round = await load();
    round.results.standings.reverse();
    stubPlotFrame();
    const user = userEvent.setup();

    const { container } = renderInDom(
      <VotesBoard base={BOARD} round={round} categories={[...CATEGORIES]} labels={CASES} />,
    );
    const table = container.querySelector('table');
    const systems = () =>
      [...(table?.querySelectorAll('tbody a') ?? [])].map((link) =>
        link.getAttribute('href')?.slice(BOARD.length, -1),
      );
    const scoreHeader = screen.getByRole('button', { name: 'Score' }).closest('th');
    const systemButton = screen.getByRole('button', { name: 'System' });
    const systemHeader = systemButton.closest('th');

    expect(systems()).toEqual([
      'acme-pro',
      'acme-large',
      'acme-small',
      'acme-flash',
      'acme-mini',
      'acme-tiny',
    ]);
    expect(scoreHeader?.getAttribute('aria-sort')).toBe('descending');

    await user.click(systemButton);

    const alphabetical = [
      'acme-flash',
      'acme-large',
      'acme-mini',
      'acme-pro',
      'acme-small',
      'acme-tiny',
    ];
    expect(systems()).toEqual(alphabetical);
    expect(systemHeader?.getAttribute('aria-sort')).toBe('ascending');
    expect(scoreHeader?.hasAttribute('aria-sort')).toBe(false);

    await user.click(systemButton);

    expect(systems()).toEqual(alphabetical.toReversed());
    expect(systemHeader?.getAttribute('aria-sort')).toBe('descending');
  });

  test('shows descriptive diagnostics and unavailable population inference', async () => {
    const html = render(await load());

    expect(stats(html)).toEqual([
      ['Votes', '340 over 10 sessions, 20 left out'],
      ['Rater agreement', '83% on 23 repeats'],
      ['Split-half τ', '0.60'],
      ['Length bias', 'Favors shorter, 6.5'],
    ]);
    expect(statHints(html)).toEqual([
      '20 left out',
      'Rater agreement',
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
    expect(stats(html)[0]).toEqual(['Votes', '340 over 10 sessions']);
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
      standing.decisive_votes = round.results.rules.preliminary_decisive_votes;
    }

    const html = render(round);

    expect(legend(html)).toBe('Point score Score below the axis Anchor, 1,000');
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
    round.systems['acme-new'] = { display_name: 'Acme New', fingerprint: null };

    const html = render(round);

    expect(html).toMatch(/<span class="sr-only">Anchor<\/span>/);
    expect(text(html)).toMatch(/Acme Large Anchor/);
    expect(legend(html)).toContain('Anchor, 1,000');
  });

  test('names the reference the stand-in anchor replaces', async () => {
    const { round, results, systems, closed_at } = await load();
    results.reference = 'acme-new';
    systems['acme-new'] = { display_name: 'Acme New', fingerprint: null };
    stubPlotFrame();
    renderInDom(
      <VotesBoard
        base={BOARD}
        round={{ round, results, systems, closed_at }}
        categories={[...CATEGORIES]}
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

  test("groups the digits of each system's vote count", async () => {
    const round = await load();
    round.results.standings[0].votes = 1234;

    const row = [...parse(render(round)).querySelectorAll('tbody tr')].find((item) =>
      item.textContent.includes('Acme Pro'),
    );

    expect([...(row?.querySelectorAll('td') ?? [])].map((cell) => cell.textContent)).toContain(
      '1,234',
    );
  });

  test("labels and titles each system's point with its display name and score", async () => {
    const html = render(await load());

    const point = parse(html).querySelector('[role="img"][aria-label="Acme Pro: 1,004"]');
    expect(point?.getAttribute('title')).toBe('Acme Pro: 1,004');
    expect(point?.querySelector('[data-slot="score-track"]')).not.toBeNull();
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
      'Repeat consistency is below the recorded 70% floor.',
    );
  });

  test('keeps population inference unavailable for a preliminary round', async () => {
    const html = render(await load('report-early'));

    expect(stats(html)[0]).toEqual(['Votes', '34 over 1 session, 2 left out']);
    expect(stats(html).map(([label]) => label)).not.toContain('Leader');
    expect(text(html)).toContain('Uncertainty for new cases and voters is unavailable');
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

    const header = parse(render(await load())).querySelector('thead');
    for (const axis of header?.querySelectorAll('[aria-hidden="true"]') ?? []) {
      axis.remove();
    }

    expect(text(header?.innerHTML ?? '')).toBe(
      ' System Point score Score Votes What Votes means Both bad all categories What Both bad means Gate failures all categories What Gate failures means ',
    );
  });

  test('shows the family board for a category the round does not have', async () => {
    search.set('category', 'missing');

    const html = render(await load());

    expect(text(head(html))).toMatch(/^ Rank What Rank means System /);
    expect(text(html)).toContain('1 Acme Pro 1,004 114');
  });

  test('labels each category board, header, and filter option in family order', async () => {
    const html = render(await load(), undefined, CASES, ['reply', 'message', 'docs']);
    const document = parse(html);
    const options = [...document.querySelectorAll('select')]
      .map((select) =>
        [...select.querySelectorAll('option')].map((option) => text(option.innerHTML).trim()),
      )
      .find((labels) => labels.includes('All categories'));
    const categoryTable = [...document.querySelectorAll('table')][1];
    const headers = [...categoryTable.querySelectorAll('thead th')].map((cell) =>
      text(cell.innerHTML).trim(),
    );
    const captions = [...document.querySelectorAll('figcaption')].map((caption) =>
      text(caption.innerHTML).trim(),
    );

    expect(options).toEqual(['All categories', 'Reply', 'Message', 'Docs']);
    expect(headers).toEqual(['System', 'Reply', 'Message', 'Docs']);
    expect(captions.filter((caption) => caption.includes('votes'))).toEqual([
      expect.stringMatching(/^Reply\b/),
      expect.stringMatching(/^Message\b/),
      expect.stringMatching(/^Docs\b/),
    ]);
    expect(text(html)).not.toMatch(/\breply\b|\bmessage\b|\bdocs\b/);
  });

  test('keeps the category ID in the search parameter while the option shows the label', async () => {
    const report = await load();
    const categoryBoard = report.results.categories.find((board) => board.category === 'message');
    if (categoryBoard === undefined) {
      throw new Error('the fixture has a message category');
    }
    categoryBoard.standings = [{ ...categoryBoard.standings[0], score: 1100 }];
    const { round, results, systems, closed_at } = report;
    stubPlotFrame();
    const user = userEvent.setup();
    renderInDom(
      <VotesBoard
        base={BOARD}
        round={{ round, results, systems, closed_at }}
        categories={[...CATEGORIES]}
        labels={CASES}
      />,
    );
    const select = screen.getByLabelText<HTMLSelectElement>('Category');

    await user.selectOptions(select, 'category:message');

    expect(search.get('category')).toBe('message');
    const selected = render(report);
    expect(text(head(selected))).toMatch(/^ System /);
    expect(text(parse(selected).querySelector('tbody')?.innerHTML ?? '')).toContain('1,100');

    const aggregate = screen.getByRole<HTMLOptionElement>('option', { name: 'All categories' });
    await user.selectOptions(select, aggregate.value);

    expect(search.has('category')).toBe(false);
    expect(text(head(render(report)))).toMatch(/^ Rank What Rank means System /);
  });

  test.each([
    ['Point score', 'max-lg:hidden'],
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

  test('shows the category legend where the leaderboard legend hides with the point column', async () => {
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
      const { round, results, systems, closed_at } = await load();
      stubPlotFrame();
      renderInDom(
        <VotesBoard
          base={BOARD}
          round={{ round, results, systems, closed_at }}
          categories={[...CATEGORIES]}
          labels={boardLabels({ render })}
        />,
      );

      await userEvent.setup().click(screen.getByRole('button', { name: score }));

      expect((await screen.findByRole('dialog', { name: 'Acme Pro' })).textContent).toContain(
        details,
      );
    },
  );

  test("draws each system's score, coverage, and cut before hydration, with the anchor's rule through every row", async () => {
    const round = await load();
    round.results.reference = 'acme-new';
    round.systems['acme-new'] = { display_name: 'Acme New', fingerprint: null };

    const drawn = tracks(render(round));

    const anchor = drawn['Acme Large'].dotAt;
    expect(anchor).not.toBeNull();
    expect(Object.values(drawn).map(({ ruleAt }) => ruleAt)).toEqual(
      Object.values(drawn).map(() => anchor),
    );
    expect(
      Object.fromEntries(
        Object.entries(drawn).map(([name, { dot, arrows }]) => [name, { dot, arrows }]),
      ),
    ).toEqual({
      'Acme Pro': { dot: 'filled', arrows: [] },
      'Acme Large': { dot: 'filled', arrows: [] },
      'Acme Small': { dot: 'filled', arrows: [] },
      'Acme Flash': { dot: 'filled', arrows: [] },
      'Acme Mini': { dot: 'filled', arrows: [] },
      'Acme Tiny': { dot: 'none', arrows: ['start'] },
    });
  });

  test.each(['report', 'report-early'] as const)(
    'draws each system of the %s fixture as the static forest draws it',
    async (fixture) => {
      const round = await load(fixture);
      const { results } = round;
      const forest = renderToString(
        <Forest
          caption="Example"
          domain={forestDomain(results.standings)}
          anchorScore={results.method.anchor_score}
          rows={roundRows(() => '/', results, reportNames(round.systems), results.rules)}
        />,
      );

      const board = tracks(render(round));

      expect(Object.keys(board)).not.toEqual([]);
      expect(board).toEqual(
        Object.fromEntries(
          Object.entries(tracks(forest)).filter(([name]) => Object.hasOwn(board, name)),
        ),
      );
    },
  );

  test('draws no anchor rule in a category that leaves the anchor out', async () => {
    const round = await load();
    const message = round.results.categories.find((board) => board.category === 'message');
    if (!message) {
      throw new Error('the fixture has a message category');
    }
    message.standings = message.standings.filter((refit) => refit.system !== round.results.anchor);
    search.set('category', 'message');

    const drawn = Object.values(tracks(render(round)));

    expect(drawn).toHaveLength(message.standings.length);
    expect(drawn.map(({ ruleAt }) => ruleAt)).toEqual(drawn.map(() => null));
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

    expect(text(html)).toContain('Acme Edge Preliminary 890 41 – –');
    expect(html).not.toContain(`href="${BOARD}acme-edge/"`);
    expect(text(html)).toContain('Acme Mini 769, 38 votes, preliminary – 789');
    expect(text(html)).toContain('Acme Edge – 890, 41 votes, preliminary – Score');
  });
});
