// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { afterEach, describe, expect, test } from 'vitest';

import {
  CategoryTable,
  DefectsTable,
  HeadToHeadTable,
  RoundsTable,
  SystemStats,
} from '@/components/boards/SystemTables';
import { terms } from '@/lib/glossary';
import { reportNames } from '@/lib/systems';
import { readReport } from '@/test/fixture';

async function round() {
  const report = await readReport('report', 'votes');
  return { systems: report.systems, ...report.results };
}

describe('RoundsTable', () => {
  afterEach(cleanup);

  test("uses each historical row's own label, preliminary threshold, and method", async () => {
    const past = await round();
    const standing = { ...past.standings[0], interval: { lower: 1018, upper: 1118 } };
    const appearances = [60, 40].map((threshold, i) => ({
      round: i === 0 ? '2026-10-07' : '2026-10-21',
      reference: 'acme-large',
      anchor: 'acme-large',
      standing,
      systems: {
        ...past.systems,
        [standing.system]: {
          ...past.systems[standing.system],
          display_name: i === 0 ? 'Historical Pro' : 'Current Pro',
        },
      },
      method: { ...past.method, anchor_score: i === 0 ? 1234 : 1000 },
      rules: { ...past.rules, preliminary_half_width: threshold },
    }));
    const { container } = render(<RoundsTable appearances={appearances} />);

    const rows = [...container.querySelectorAll('tbody tr')];
    expect(rows.map((row) => row.textContent.includes('Preliminary'))).toEqual([true, false]);
    await userEvent.setup().click(screen.getAllByRole('button', { name: '1,068' })[1]);

    const historical = await screen.findByRole('dialog', { name: 'Round settings' });
    expect(historical.textContent).toContain('Historical Pro');
    expect(historical.textContent).toContain('anchor 1,234');
    expect(historical.textContent).toContain('wider than ±60 points');
    expect(historical.textContent).not.toContain('Current Pro');
  });

  test.each([
    [
      '2026-10-21',
      '20261021abcdef01, a hash of the route, effort, harness, and prompt the system ran with that round.',
    ],
    ['2026-10-07', 'This round recorded no fingerprint for the system.'],
  ])(
    'opens the fingerprint, or its absence, of round %s from its date',
    async (date, definition) => {
      const past = await round();
      const appearances = [
        { round: '2026-10-07', fingerprint: null },
        { round: '2026-10-21', fingerprint: '20261021abcdef01' },
      ].map(({ round, fingerprint }) => ({
        round,
        reference: 'acme-large',
        anchor: 'acme-large',
        systems: {
          ...past.systems,
          [past.standings[0].system]: { ...past.systems[past.standings[0].system], fingerprint },
        },
        method: past.method,
        rules: past.rules,
        standing: past.standings[0],
      }));
      render(<RoundsTable appearances={appearances} />);

      await userEvent.setup().click(screen.getByRole('button', { name: date }));

      expect(
        (
          await screen.findByRole('dialog', {
            name: past.systems[past.standings[0].system].display_name,
          })
        ).textContent,
      ).toContain(definition);
    },
  );
});

describe('vote counts', () => {
  afterEach(cleanup);

  test.each<[string, (past: Awaited<ReturnType<typeof round>>) => ReactElement, string[]]>([
    [
      'head-to-head table',
      (past) => (
        <HeadToHeadTable
          headToHead={[
            {
              ...past.head_to_head[0],
              votes: 4321,
              wins: 1234,
              losses: 1100,
              ties: 1010,
              both_bad: 977,
            },
          ]}
          names={reportNames(past.systems)}
        />
      ),
      ['4,321', '1,234', '1,100', '1,010', '977'],
    ],
    [
      'category table',
      (past) => (
        <CategoryTable
          categories={[
            { label: 'docs', refit: { ...past.categories[0].standings[0], votes: 1234 } },
          ]}
        />
      ),
      ['1,234'],
    ],
    [
      'issues table',
      (past) => (
        <DefectsTable standing={{ ...past.standings[0], votes: 5000, defects: { facts: 1234 } }} />
      ),
      ['1,234'],
    ],
    [
      'rounds table',
      (past) => (
        <RoundsTable
          appearances={[
            {
              round: '2026-10-21',
              reference: past.reference,
              anchor: past.anchor,
              systems: past.systems,
              method: past.method,
              rules: past.rules,
              standing: { ...past.standings[0], votes: 1234 },
            },
          ]}
        />
      ),
      ['1,234'],
    ],
  ])('groups the digits of every count in the %s', async (_, table, counts) => {
    const { container } = render(table(await round()));

    const cells = [...container.querySelectorAll('tbody td')].map((cell) => cell.textContent);

    expect(cells).toEqual(expect.arrayContaining(counts));
  });
});

describe('SystemStats', () => {
  afterEach(cleanup);

  test.each([
    {
      name: 'with an interval',
      interval: { lower: 950, upper: 1050 },
      expected: '1,000 (950–1,050)',
    },
    { name: 'without an interval', interval: null, expected: '1,000' },
  ])('shows the score $name', async ({ interval, expected }) => {
    const { standings } = await round();

    render(
      <SystemStats
        standing={{ ...standings[0], score: 1000, interval }}
        round="2026-10-21"
        roundHref="/writing/en/rounds/2026-10-21/"
      />,
    );

    expect(screen.getByText('Score').nextElementSibling?.textContent).toBe(expected);
  });

  test('opens what decisive means from the decisive count', async () => {
    const { standings } = await round();
    render(
      <SystemStats
        standing={{ ...standings[0], votes: 114, decisive_votes: 89 }}
        round="2026-10-21"
        roundHref="/writing/en/rounds/2026-10-21/"
      />,
    );

    await userEvent.setup().click(screen.getByRole('button', { name: '(89 decisive)' }));

    expect((await screen.findByRole('dialog', { name: 'Decisive' })).textContent).toContain(
      terms.decisive.definition,
    );
  });
});
