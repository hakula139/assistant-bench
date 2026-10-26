import { describe, expect, test } from 'vitest';

import type { FamilyConfig, VotesConfig } from '@/lib/family';
import { reportSchema, type Report } from '@/lib/report';
import { boardSlug, publicFamilies, systemPages, testsRounds, votesRounds } from '@/lib/reports';
import { FIXTURE_NAMES, readFixture } from '@/test/fixture';

const votes = (listed: boolean, displayName = 'Prose'): VotesConfig => ({
  scorer: 'votes',
  ...(listed && { aspect: 'writing' }),
  display_name: displayName,
  systems: ['acme-large', 'acme-pro'],
  categories: [],
  reference: 'acme-large',
  render: 'text',
  gates: {},
  public: listed,
});

async function report(family: string, round: string, closedAt: string): Promise<Report> {
  const parsed = reportSchema.parse(await readFixture());
  return { ...parsed, family, round, closed_at: closedAt };
}

describe('boardSlug', () => {
  test('names a board by the family ID under its aspect', () => {
    expect(boardSlug('writing-zh', 'writing')).toBe('zh');
  });

  test.each(['zh', 'writingzh'])(
    'rejects the family ID %s, which does not start with its aspect and a hyphen',
    (name) => {
      expect(() => boardSlug(name, 'writing')).toThrow(
        `family ${name}: a public family ID starts with its aspect, as in writing-<name>`,
      );
    },
  );
});

describe('publicFamilies', () => {
  test('keeps closed identity metadata independently of the current runnable names', async () => {
    const earlier = await report('writing-prose', '2026-10-07', '2026-10-07T18:00:00Z');
    const later = structuredClone(earlier);
    later.round = '2026-10-21';
    later.closed_at = '2026-10-21T18:00:00Z';
    later.systems['acme-large'].display_name = 'Renamed Large';

    const [family] = publicFamilies(
      [{ name: 'writing-prose', config: votes(true) }],
      [later, earlier],
      {},
    );
    const rounds = votesRounds(family);

    expect(rounds.map(({ systems }) => systems['acme-large'].display_name)).toEqual([
      'Acme Large',
      'Renamed Large',
    ]);
    expect(rounds.map(({ round, closed_at }) => [round, closed_at])).toEqual([
      [earlier.round, earlier.closed_at],
      [later.round, later.closed_at],
    ]);
    expect(systemPages(family).has('acme-large')).toBe(true);
  });

  test('keeps tests-scorer identities and descriptive results without a runnable system', () => {
    const closed: Report = {
      family: 'writing-prose',
      round: '2026-10-21',
      closed_at: '2026-10-21T18:00:00Z',
      systems: { retired: { display_name: 'Retired System', fingerprint: null } },
      results: {
        scorer: 'tests',
        standings: [
          {
            system: 'retired',
            trials: 4,
            errored: 1,
            pass_rate: 0.5,
            mean_cost_usd: null,
            p50_latency_sec: null,
          },
        ],
      },
    };
    const [family] = publicFamilies([{ name: 'writing-prose', config: votes(true) }], [closed], {});

    expect(testsRounds(family)).toEqual([
      {
        round: closed.round,
        closed_at: closed.closed_at,
        systems: closed.systems,
        results: closed.results,
      },
    ]);
    expect(votesRounds(family)).toEqual([]);
  });

  test('lists the families by name', () => {
    const families = publicFamilies(
      [
        { name: 'writing-zh', config: votes(true, 'Chinese Prose') },
        { name: 'writing-en', config: votes(true, 'English Prose') },
      ],
      [],
      FIXTURE_NAMES,
    );

    expect(families.map((family) => family.name)).toEqual(['writing-en', 'writing-zh']);
  });

  test('lists a public family before its first round closes', () => {
    const families = publicFamilies(
      [{ name: 'writing-prose', config: votes(true) }],
      [],
      FIXTURE_NAMES,
    );

    expect(families).toEqual([
      {
        name: 'writing-prose',
        title: 'Writing · Prose',
        aspect: 'writing',
        config: votes(true),
        rounds: [],
        systemNames: FIXTURE_NAMES,
      },
    ]);
  });

  test('leaves out a family that is not public, with its reports', async () => {
    const smoke = await report('smoke-text', '2026-10-21', '2026-10-21T18:00:00Z');

    const families = publicFamilies(
      [
        { name: 'smoke-text', config: votes(false, 'Smoke') },
        { name: 'writing-prose', config: votes(true) },
      ],
      [smoke],
      FIXTURE_NAMES,
    );

    expect(families.map((family) => [family.name, family.rounds.length])).toEqual([
      ['writing-prose', 0],
    ]);
  });

  test('rejects two families sharing a full name, even one that is not public', () => {
    const configs = [
      { name: 'writing-prose', config: votes(true, 'English') },
      { name: 'smoke-text', config: { ...votes(false, 'English'), aspect: 'writing' } },
    ] satisfies { name: string; config: FamilyConfig }[];

    expect(() => publicFamilies(configs, [], FIXTURE_NAMES)).toThrow(
      'families writing-prose and smoke-text share the full name Writing · English',
    );
  });

  test('accepts a short name that a family without an aspect already uses', () => {
    const configs = [
      { name: 'writing-prose', config: votes(true, 'English') },
      { name: 'smoke-text', config: votes(false, 'English') },
    ];

    const families = publicFamilies(configs, [], FIXTURE_NAMES);

    expect(families.map((family) => family.title)).toEqual(['Writing · English']);
  });

  test('rejects a public family without an aspect', () => {
    const configs = [{ name: 'prose', config: { ...votes(true), aspect: undefined } }];

    expect(() => publicFamilies(configs, [], FIXTURE_NAMES)).toThrow(
      'public family prose has no aspect',
    );
  });
});

describe('systemPages', () => {
  test('gives a page to every system a voted round placed', async () => {
    const round = await report('writing-prose', '2026-10-21', '2026-10-21T18:00:00Z');
    const [family] = publicFamilies(
      [{ name: 'writing-prose', config: votes(true) }],
      [round],
      FIXTURE_NAMES,
    );

    const pages = systemPages(family);

    expect([...pages].toSorted()).toEqual(round.results.standings.map((s) => s.system).toSorted());
  });
});
