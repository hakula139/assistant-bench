import { describe, expect, test } from 'vitest';

import type { FamilyConfig, VotesConfig } from '@/lib/family';
import { reportSchema, type Report } from '@/lib/report';
import { boardSlug, listFamilies, systemPages } from '@/lib/reports';
import { FIXTURE_NAMES, readFixture } from '@/test/fixture';

const votes = (listed: boolean, displayName = 'Prose'): VotesConfig => ({
  scorer: 'votes',
  ...(listed && { aspect: 'writing' }),
  display_name: displayName,
  systems: ['acme-large', 'acme-pro'],
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

  test('rejects a family ID that does not start with its aspect and a hyphen', () => {
    expect(() => boardSlug('zh', 'writing')).toThrow(
      'family zh: a public family ID starts with its aspect, as in writing-<name>',
    );
    expect(() => boardSlug('writingzh', 'writing')).toThrow(
      'family writingzh: a public family ID starts with its aspect, as in writing-<name>',
    );
  });
});

describe('listFamilies', () => {
  test('pairs each public family with its rounds, oldest first', async () => {
    const later = await report('writing-prose', '2026-10-21', '2026-10-21T18:00:00Z');
    const earlier = await report('writing-prose', '2026-10-07', '2026-10-07T18:00:00Z');

    const families = listFamilies(
      [{ name: 'writing-prose', config: votes(true) }],
      [later, earlier],
      FIXTURE_NAMES,
    );

    expect(families.map((family) => family.rounds.map(({ round }) => round))).toEqual([
      ['2026-10-07', '2026-10-21'],
    ]);
  });

  test('lists the families by name', () => {
    const families = listFamilies(
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
    const families = listFamilies(
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

    const families = listFamilies(
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

    expect(() => listFamilies(configs, [], FIXTURE_NAMES)).toThrow(
      'families writing-prose and smoke-text share the full name Writing · English',
    );
  });

  test('accepts a short name that a family without an aspect already uses', () => {
    const configs = [
      { name: 'writing-prose', config: votes(true, 'English') },
      { name: 'smoke-text', config: votes(false, 'English') },
    ];

    const families = listFamilies(configs, [], FIXTURE_NAMES);

    expect(families.map((family) => family.title)).toEqual(['Writing · English']);
  });

  test('rejects a public family without an aspect', () => {
    const configs = [{ name: 'prose', config: { ...votes(true), aspect: undefined } }];

    expect(() => listFamilies(configs, [], FIXTURE_NAMES)).toThrow(
      'public family prose has no aspect',
    );
  });
});

describe('systemPages', () => {
  test('gives a page to every system a voted round placed', async () => {
    const round = await report('writing-prose', '2026-10-21', '2026-10-21T18:00:00Z');
    const [family] = listFamilies(
      [{ name: 'writing-prose', config: votes(true) }],
      [round],
      FIXTURE_NAMES,
    );

    const pages = systemPages(family);

    expect([...pages].toSorted()).toEqual(round.results.standings.map((s) => s.system).toSorted());
  });
});
