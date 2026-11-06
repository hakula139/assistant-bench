// @vitest-environment jsdom
import { cleanup, render as renderInDom, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderToString } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, test } from 'vitest';

import { VotesBoard } from '@/components/boards/VotesBoard';
import { Forest } from '@/components/site/Forest';
import { forestDomain, roundRows } from '@/lib/forest';
import { boardLabels } from '@/lib/method';
import type { PassRate } from '@/lib/report';
import type { VotesReport } from '@/lib/reports';
import { reportNames } from '@/lib/systems';
import { FIXTURE_NAMES, readReport, shownSlugs } from '@/test/fixture';
import { stubPlotFrame } from '@/test/plot';
import { trackDrawing, type TrackDrawing } from '@/test/track';

async function load(name: 'report' | 'report-early' = 'report'): Promise<VotesReport> {
  const round = await readReport(name, 'votes');
  if (name === 'report') {
    for (const standing of round.results.standings) {
      const half = standing.system === 'acme-tiny' ? 80 : 20;
      standing.interval = { lower: standing.score - half, upper: standing.score + half };
    }
  }
  return round;
}

const PASS_RATES: Record<string, PassRate | null> = {
  'acme-mini': { rate: 0.9, lower: 0.6, upper: 1 },
  'acme-flash': { rate: 0.75, lower: 0.45, upper: 0.95 },
  'acme-pro': { rate: 0.5, lower: 0.2, upper: 0.8 },
  'acme-small': { rate: 0.25, lower: 0.05, upper: 0.55 },
  'acme-large': { rate: 0, lower: 0, upper: 0.3 },
  'acme-tiny': null,
};

/**
 * Loads the round with the pass rates that a family with task-test rewards reports, where none of
 * `acme-tiny`'s outputs has a reward.
 */
async function loadTested(): Promise<VotesReport> {
  const round = await load();
  for (const standing of round.results.standings) {
    standing.pass_rate = PASS_RATES[standing.system];
  }
  return round;
}

const BOARD = '/writing/arena/';

const CASES = boardLabels({ render: 'text' });
const DIFFS = boardLabels({ render: 'diff' });

function render(round: VotesReport, names?: Record<string, string>, labels = CASES): string {
  const reversed = { ...round.results, standings: round.results.standings.toReversed() };
  const systems = names
    ? Object.fromEntries(
        Object.entries(names).map(([id, display_name]) => [
          id,
          { display_name, fingerprint: null },
        ]),
      )
    : round.systems;
  const { container, unmount } = renderInDom(
    <VotesBoard base={BOARD} round={{ ...round, systems, results: reversed }} labels={labels} />,
  );
  const html = container.innerHTML;
  unmount();
  return html;
}

// A tag, whose quoted attribute values may hold `>`.
const text = (html: string) =>
  html.replace(/<(?:[^>"']|"[^"]*"|'[^']*')*>/g, ' ').replace(/\s+/g, ' ');

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
  beforeEach(() => {
    stubPlotFrame();
    window.history.replaceState(null, '', '/');
  });
  afterEach(cleanup);

  test('draws the labels, preliminary marks, and copy of the round it shows', async () => {
    const earlier = await load('report-early');
    const current = await load();
    earlier.systems['acme-pro'].display_name = 'Historical Pro';
    current.systems['acme-pro'].display_name = 'Current Pro';
    earlier.results.rules.preliminary_half_width = 75;
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
      pro.interval = { lower: pro.score - 60, upper: pro.score + 60 };
    }
    const draw = (round: VotesReport) =>
      renderToString(<VotesBoard base={BOARD} round={round} labels={CASES} />);

    const historical = draw(earlier);

    const historicalRow = [...parse(historical).querySelectorAll('tbody tr')].find((row) =>
      row.querySelector(`a[href="${BOARD}acme-pro/"]`),
    );
    expect(historicalRow?.textContent).toContain('Historical Pro');
    expect(historicalRow?.textContent).not.toContain('Preliminary');
    expect(text(historical)).not.toContain('Current Pro');
    expect(text(historical)).toContain('anchor 1,234');
    expect(text(historical)).toContain('wider than ±75 points');

    const latest = draw(current);

    const currentRow = [...parse(latest).querySelectorAll('tbody tr')].find((row) =>
      row.querySelector(`a[href="${BOARD}acme-pro/"]`),
    );
    expect(currentRow?.textContent).toContain('Current ProPreliminary');
    expect(text(latest)).not.toContain('Historical Pro');
    expect(text(latest)).toContain('wider than ±50 points');
  });

  test('starts in score order and sorts systems in both directions', async () => {
    const round = await load();
    round.results.standings.reverse();
    const user = userEvent.setup();

    const { container } = renderInDom(<VotesBoard base={BOARD} round={round} labels={CASES} />);
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

  test('shows the vote counts and descriptive diagnostics', async () => {
    const html = render(await load());

    expect(stats(html)).toEqual([
      ['Votes', '315 from 7 voters on 30 cases, 42 left out'],
      ['Effective votes', '286'],
      ['Split-half τ', '0.40'],
      ['Length bias', 'Favors longer, 29.9'],
    ]);
    expect(statHints(html)).toEqual([
      '42 left out',
      'Effective votes',
      'Split-half τ',
      'Length bias',
    ]);
    expect(parse(html).querySelector('[role="status"]')?.textContent).toMatch(/^Closed /);
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
    expect(stats(html)[0]).toEqual(['Votes', '315 from 7 voters on 30 cases']);
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
      standing.interval = { lower: standing.score - 20, upper: standing.score + 20 };
    }

    const html = render(round);

    expect(legend(html)).toBe('Point score Score interval Score below the axis Anchor, 1,000');
  });

  test('marks only the anchor and the preliminary systems', async () => {
    const html = render(await load());
    const leaderboard = html.slice(0, html.indexOf('>Categories</h2>'));

    const marked = (status: string) =>
      [...text(leaderboard).matchAll(new RegExp(`(Acme [A-Z][a-z]+) ${status} `, 'g'))].map(
        (m) => m[1],
      );

    expect(marked('Anchor')).toEqual(['Acme Large']);
    expect(marked('Preliminary')).toEqual(['Acme Tiny']);
  });

  test.each([
    [[], ', and a case-clustered Wald test separates it from every other system.'],
    [['acme-large'], ', but a case-clustered Wald test cannot separate it from Acme Large.'],
  ])('names the systems tied with the leader: %j', async (tied, decision) => {
    const round = await load();
    if (round.results.decision === null) {
      throw new Error('Fixture decides its leader');
    }
    round.results.decision.tied = tied;

    const html = render(round);

    expect(text(html)).toContain(`Acme Pro has the top score${decision}`);
  });

  test("leaves the decision out of a category's board", async () => {
    window.history.replaceState(null, '', '/?category=message');

    const html = render(await load());

    expect(text(html)).not.toContain('has the top score');
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
    const { family, round, results, systems, closed_at } = await load();
    results.reference = 'acme-new';
    systems['acme-new'] = { display_name: 'Acme New', fingerprint: null };
    renderInDom(
      <VotesBoard
        base={BOARD}
        round={{ family, round, results, systems, closed_at }}
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

  test("labels and titles each system's point with its display name, score, and interval", async () => {
    const html = render(await load());

    const summary = 'Acme Pro: 1,068 (1,048–1,088)';
    const point = parse(html).querySelector(`[role="img"][aria-label="${summary}"]`);
    expect(point?.getAttribute('title')).toBe(summary);
    expect(point?.querySelector('[data-slot="score-track"]')).not.toBeNull();
  });

  test.each(['all', 'message'])(
    'shows display names wherever the %s board names a system',
    async (category) => {
      const round = await load();
      Object.assign(round.results.standings[0], { mean_cost_usd: null, cost_gap: 'unpriced' });
      window.history.replaceState(null, '', `/?category=${category}`);

      const html = render(round);

      expect(text(html)).toContain(`leaving out ${FIXTURE_NAMES['acme-pro']} (unpriced)`);
      expect(shownSlugs(html)).toEqual([]);
    },
  );

  test('names unreported usage when the cost scatter leaves out a system', async () => {
    const round = await load();
    Object.assign(round.results.standings[0], { mean_cost_usd: null, cost_gap: 'unreported' });

    const html = render(round);

    expect(text(html)).toContain(', leaving out Acme Pro (usage unreported) Median latency');
  });

  test('counts the single voter of an early round', async () => {
    const html = render(await load('report-early'));

    expect(stats(html)[0]).toEqual(['Votes', '33 from 1 voter on 30 cases, 2 left out']);
  });

  test('leaves out the cost chart when no system is priced', async () => {
    const round = await load();
    for (const standing of round.results.standings) {
      standing.mean_cost_usd = null;
    }

    const html = render(round);

    expect(text(html)).not.toContain('Mean cost per output');
    expect(text(html)).toContain('Median latency');
  });

  test('leaves out the latency chart and dashes the latency when no system is timed', async () => {
    const round = await load();
    for (const standing of round.results.standings) {
      standing.latency_sec = null;
    }

    const html = render(round);

    expect(text(html)).not.toContain('Median latency');
    expect(text(html)).toContain('Mean cost per output');
    expect(text(html)).toMatch(/Acme Pro [\d,.]+ \$[\d.]+ – /);
  });

  test('drops the rank column in a category and marks the columns that stay family-wide', async () => {
    window.history.replaceState(null, '', '/?category=message');

    const header = parse(render(await load())).querySelector('thead');
    for (const axis of header?.querySelectorAll('[aria-hidden="true"]') ?? []) {
      axis.remove();
    }

    expect(text(header?.innerHTML ?? '')).toBe(
      ' System Point score Score Votes What Votes means Both bad all categories What Both bad means Gate failures all categories What Gate failures means ',
    );
  });

  test('replaces the gate column with a family-wide pass rate for a tested family', async () => {
    window.history.replaceState(null, '', '/?category=message');

    const header = parse(render(await loadTested(), undefined, DIFFS)).querySelector('thead');
    for (const axis of header?.querySelectorAll('[aria-hidden="true"]') ?? []) {
      axis.remove();
    }

    expect(text(header?.innerHTML ?? '')).toBe(
      ' System Point score Score Votes What Votes means Both bad all categories What Both bad means Pass rate all categories What Pass rate means ',
    );
  });

  test('sorts a tested family by pass rate with its interval, highest first and missing last', async () => {
    const { container } = renderInDom(
      <VotesBoard base={BOARD} round={await loadTested()} labels={DIFFS} />,
    );
    const rows = () =>
      [...(container.querySelector('table')?.querySelectorAll('tbody tr') ?? [])].map((row) => [
        row.querySelector('a')?.getAttribute('href')?.slice(BOARD.length, -1),
        text(row.lastElementChild?.innerHTML ?? '').trim(),
      ]);

    await userEvent.setup().click(screen.getByRole('button', { name: 'Pass rate' }));

    expect(rows()).toEqual([
      ['acme-mini', '90% 60–100%'],
      ['acme-flash', '75% 45–95%'],
      ['acme-pro', '50% 20–80%'],
      ['acme-small', '25% 5–55%'],
      ['acme-large', '0% 0–30%'],
      ['acme-tiny', '–'],
    ]);
  });

  test('shows the family board for a category the round does not have', async () => {
    window.history.replaceState(null, '', '/?category=missing');

    const html = render(await load());

    expect(text(head(html))).toMatch(/^ Rank What Rank means System /);
    expect(text(html)).toContain('1 Acme Pro 1,068 1,048–1,088 98');
  });

  test('labels each category board, header, and filter option in report order', async () => {
    const round = await load();
    round.results.categories.reverse();
    const html = render(round);
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
    const { family, round, results, systems, closed_at } = report;
    const user = userEvent.setup();
    renderInDom(
      <VotesBoard
        base={BOARD}
        round={{ family, round, results, systems, closed_at }}
        labels={CASES}
      />,
    );
    const select = screen.getByLabelText<HTMLSelectElement>('Category');

    await user.selectOptions(select, 'message');

    expect(new URLSearchParams(window.location.search).get('category')).toBe('message');
    const selected = render(report);
    expect(text(head(selected))).toMatch(/^ System /);
    expect(text(parse(selected).querySelector('tbody')?.innerHTML ?? '')).toContain('1,100');

    const aggregate = screen.getByRole<HTMLOptionElement>('option', { name: 'All categories' });
    await user.selectOptions(select, aggregate.value);

    expect(window.location.search).toBe('');
    expect(text(head(render(report)))).toMatch(/^ Rank What Rank means System /);
  });

  test.each([
    ['Point score', 'max-lg:hidden', load],
    ['Votes', 'max-md:hidden', load],
    ['Both bad', 'max-md:hidden', load],
    ['Gate failures', 'max-md:hidden', load],
    ['Pass rate', 'max-md:hidden', loadTested],
    ['Score', null, load],
  ] as const)('hides the %s column on narrow screens with %s', async (name, hidden, loadRound) => {
    const leaderboard = parse(render(await loadRound())).querySelector('table');

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

  test('keeps the category legend at every width, while the leaderboard legend hides with the point column', async () => {
    const document = parse(render(await load()));

    const legends = [...document.querySelectorAll('ul[aria-label="Legend"]')].map((list) => [
      list.className.split(' ').find((rule) => rule.endsWith(':hidden')),
      list.closest('section')?.querySelector('h2')?.textContent ?? null,
      text(list.innerHTML).trim(),
    ]);

    expect(legends).toEqual([
      ['max-lg:hidden', null, 'Point score Score interval Score below the axis Anchor, 1,000'],
      [undefined, 'Categories', expect.stringContaining('Preliminary')],
    ]);
  });

  test.each([
    ['all', 'text', 'text', '1,068', load, '98 votes, both bad 8%, gate failures 0%.'],
    [
      'message',
      'text',
      'text',
      '1,036',
      load,
      '33 votes, both bad 8%, gate failures 0% across all categories.',
    ],
    ['all', 'diff', 'diff', '1,068', load, '98 votes, both bad 8%, failed tests 0%.'],
    [
      'all',
      'tested diff',
      'diff',
      '1,068',
      loadTested,
      '98 votes, both bad 8%, pass rate 50% (20–80%).',
    ],
  ] as const)(
    "opens a system's votes and rates from its score on the %s board of a %s family",
    async (category, _family, output, score, loadRound, details) => {
      window.history.replaceState(null, '', `/?category=${category}`);
      renderInDom(
        <VotesBoard
          base={BOARD}
          round={await loadRound()}
          labels={boardLabels({ render: output })}
        />,
      );

      await userEvent.setup().click(screen.getByRole('button', { name: score }));

      expect((await screen.findByRole('dialog', { name: 'Acme Pro' })).textContent).toContain(
        details,
      );
    },
  );

  test("draws each system's score, interval, and cut before hydration, with the anchor's rule through every row", async () => {
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
        Object.entries(drawn).map(([name, { dot, arrows, interval }]) => [
          name,
          { dot, arrows, interval: interval !== null },
        ]),
      ),
    ).toEqual({
      'Acme Pro': { dot: 'filled', arrows: [], interval: true },
      'Acme Large': { dot: 'filled', arrows: [], interval: true },
      'Acme Small': { dot: 'filled', arrows: [], interval: true },
      'Acme Flash': { dot: 'filled', arrows: [], interval: true },
      'Acme Mini': { dot: 'filled', arrows: [], interval: true },
      'Acme Tiny': { dot: 'none', arrows: ['start'], interval: true },
    });
    const [left, width] = (drawn['Acme Pro'].interval ?? []).map(Number.parseFloat);
    expect(left + width / 2).toBeCloseTo(Number.parseFloat(drawn['Acme Pro'].dotAt ?? ''));
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

  test('keeps every category row and the systems only the category ranks', async () => {
    const round = await load();
    const message = round.results.categories.find((board) => board.category === 'message');
    const refit = message?.standings.find((item) => item.system === 'acme-mini');
    if (!message || !refit) {
      throw new Error('the fixture refits acme-mini in message');
    }
    refit.system = 'acme-edge';
    message.unranked = ['acme-nano'];
    window.history.replaceState(null, '', '/?category=message');

    const html = render(round);

    expect(text(html)).toContain('Acme Edge Preliminary 847 31 – –');
    expect(html).not.toContain(`href="${BOARD}acme-edge/"`);
    expect(text(html)).toContain('Acme Mini 857 36 votes no votes 934 36 votes');
    expect(text(html)).toContain('Acme Edge unranked 847 31 votes no votes Score');
  });
});
