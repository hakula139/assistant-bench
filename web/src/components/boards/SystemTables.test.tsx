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
  VotesStat,
} from '@/components/boards/SystemTables';
import { terms } from '@/lib/glossary';
import { reportSchema } from '@/lib/report';
import { reportNames } from '@/lib/systems';
import { readFixture } from '@/test/fixture';

async function round() {
  const report = reportSchema.parse(await readFixture());
  if (report.results.scorer !== 'votes') {
    throw new Error('the fixture is a votes report');
  }
  return { systems: report.systems, ...report.results };
}

describe('RoundsTable', () => {
  afterEach(cleanup);

  test("uses each historical row's own label, coverage threshold, and method", async () => {
    const past = await round();
    const standing = { ...past.standings[0], decisive_votes: 20 };
    const appearances = [12, 40].map((threshold, i) => ({
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
      rules: { ...past.rules, preliminary_decisive_votes: threshold },
    }));
    const { container } = render(<RoundsTable appearances={appearances} />);

    const rows = [...container.querySelectorAll('tbody tr')];
    expect(rows.map((row) => row.textContent.includes('Preliminary'))).toEqual([true, false]);
    await userEvent.setup().click(screen.getAllByRole('button', { name: '1,068' })[1]);

    const historical = await screen.findByRole('dialog', { name: 'Round settings' });
    expect(historical.textContent).toContain('Historical Pro');
    expect(historical.textContent).toContain('anchor 1,234');
    expect(historical.textContent).toContain('12 raw decisive votes');
    expect(historical.textContent).not.toContain('Current Pro');
  });

  test("opens a round's fingerprint from its date, and leaves a round without one plain", async () => {
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

    await userEvent.setup().click(screen.getByRole('button', { name: '2026-10-21' }));

    expect(
      (
        await screen.findByRole('dialog', {
          name: past.systems[past.standings[0].system].display_name,
        })
      ).textContent,
    ).toContain(
      '20261021abcdef01, a hash of the route, effort, harness, and prompt the system ran with that round.',
    );
    expect(screen.queryByRole('button', { name: '2026-10-07' })).toBeNull();
  });
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
          rules={past.rules}
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

describe('VotesStat', () => {
  afterEach(cleanup);

  test('opens what decisive means from the decisive count', async () => {
    render(<VotesStat votes={114} decisive={89} />);

    await userEvent.setup().click(screen.getByRole('button', { name: '(89 decisive)' }));

    expect((await screen.findByRole('dialog', { name: 'Decisive' })).textContent).toContain(
      terms.decisive.definition,
    );
  });
});
