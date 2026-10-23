// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, test } from 'vitest';

import { RoundsTable, VotesStat } from '@/components/boards/SystemTables';
import { terms } from '@/lib/glossary';
import { reportSchema, type Standing } from '@/lib/report';
import { FIXTURE_NAMES, readFixture } from '@/test/fixture';

async function standing(): Promise<Standing> {
  const report = reportSchema.parse(await readFixture());
  if (report.results.scorer !== 'votes') {
    throw new Error('the fixture is a votes report');
  }
  return report.results.standings[0];
}

describe('RoundsTable', () => {
  afterEach(cleanup);

  test("opens a round's fingerprint from its date, and leaves a round without one plain", async () => {
    const past = await standing();
    const appearances = [
      { round: '2026-10-07', fingerprint: null },
      { round: '2026-10-21', fingerprint: '20261021abcdef01' },
    ].map(({ round, fingerprint }) => ({
      round,
      reference: 'acme-large',
      anchor: 'acme-large',
      standing: { ...past, fingerprint },
    }));
    render(<RoundsTable appearances={appearances} names={FIXTURE_NAMES} />);

    await userEvent.setup().click(screen.getByRole('button', { name: '2026-10-21' }));

    expect((await screen.findByRole('dialog', { name: 'Fingerprint' })).textContent).toContain(
      '20261021abcdef01, a hash of the route, effort, harness, and prompt the system ran with that round.',
    );
    expect(screen.queryByRole('button', { name: '2026-10-07' })).toBeNull();
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
