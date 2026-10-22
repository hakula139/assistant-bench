import { describe, expect, test } from 'vitest';

import type { FamilyConfig } from '@/lib/family';
import { reportSchema, type Report } from '@/lib/report';
import { listFamilies } from '@/lib/reports';
import { readFixture } from '@/test/fixture';

const votes = (listed: boolean): FamilyConfig => ({
  scorer: 'votes',
  systems: ['acme-large', 'acme-pro'],
  reference: 'acme-large',
  public: listed,
});

async function report(family: string, round: string, closedAt: string): Promise<Report> {
  const parsed = reportSchema.parse(await readFixture());
  return { ...parsed, family, round, closed_at: closedAt };
}

describe('listFamilies', () => {
  test('pairs each public family with its rounds, oldest first', async () => {
    const later = await report('prose', '2026-10-21', '2026-10-21T18:00:00Z');
    const earlier = await report('prose', '2026-10-07', '2026-10-07T18:00:00Z');

    const families = listFamilies([{ name: 'prose', config: votes(true) }], [later, earlier]);

    expect(families.map((family) => family.rounds.map(({ round }) => round))).toEqual([
      ['2026-10-07', '2026-10-21'],
    ]);
  });

  test('lists a public family before its first round closes', () => {
    const families = listFamilies([{ name: 'prose', config: votes(true) }], []);

    expect(families).toEqual([{ name: 'prose', config: votes(true), rounds: [] }]);
  });

  test('leaves out a family that is not public, with its reports', async () => {
    const smoke = await report('smoke-text', '2026-10-21', '2026-10-21T18:00:00Z');

    const families = listFamilies(
      [
        { name: 'smoke-text', config: votes(false) },
        { name: 'prose', config: votes(true) },
      ],
      [smoke],
    );

    expect(families.map((family) => [family.name, family.rounds.length])).toEqual([['prose', 0]]);
  });
});
