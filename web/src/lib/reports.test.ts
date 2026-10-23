import { describe, expect, test } from 'vitest';

import type { FamilyConfig, VotesConfig } from '@/lib/family';
import { reportSchema, type Report } from '@/lib/report';
import {
  boardPath,
  boardSlug,
  listFamilies,
  redirectLines,
  systemPages,
  systemPath,
} from '@/lib/reports';
import { FIXTURE_NAMES, readFixture } from '@/test/fixture';

const votes = (listed: boolean, displayName = 'Prose'): VotesConfig => ({
  scorer: 'votes',
  ...(listed && { aspect: 'technical-writing' }),
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

describe('listFamilies', () => {
  test('pairs each public family with its rounds, oldest first', async () => {
    const later = await report('prose', '2026-10-21', '2026-10-21T18:00:00Z');
    const earlier = await report('prose', '2026-10-07', '2026-10-07T18:00:00Z');

    const families = listFamilies(
      [{ name: 'prose', config: votes(true) }],
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
        { name: 'prose-zh', config: votes(true, 'Chinese Prose') },
        { name: 'prose-en', config: votes(true, 'English Prose') },
      ],
      [],
      FIXTURE_NAMES,
    );

    expect(families.map((family) => family.name)).toEqual(['prose-en', 'prose-zh']);
  });

  test('lists a public family before its first round closes', () => {
    const families = listFamilies([{ name: 'prose', config: votes(true) }], [], FIXTURE_NAMES);

    expect(families).toEqual([
      {
        name: 'prose',
        title: 'Technical Writing · Prose',
        aspect: 'technical-writing',
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
        { name: 'prose', config: votes(true) },
      ],
      [smoke],
      FIXTURE_NAMES,
    );

    expect(families.map((family) => [family.name, family.rounds.length])).toEqual([['prose', 0]]);
  });

  test('rejects two families sharing a full name, even one that is not public', () => {
    const configs = [
      { name: 'prose', config: votes(true, 'English') },
      { name: 'smoke-text', config: { ...votes(false, 'English'), aspect: 'technical-writing' } },
    ] satisfies { name: string; config: FamilyConfig }[];

    expect(() => listFamilies(configs, [], FIXTURE_NAMES)).toThrow(
      'families prose and smoke-text share the full name Technical Writing · English',
    );
  });

  test('accepts a short name that a family without an aspect already uses', () => {
    const configs = [
      { name: 'prose', config: votes(true, 'English') },
      { name: 'smoke-text', config: votes(false, 'English') },
    ];

    const families = listFamilies(configs, [], FIXTURE_NAMES);

    expect(families.map((family) => family.title)).toEqual(['Technical Writing · English']);
  });

  test('rejects a public family without an aspect', () => {
    const configs = [{ name: 'prose', config: { ...votes(true), aspect: undefined } }];

    expect(() => listFamilies(configs, [], FIXTURE_NAMES)).toThrow(
      'public family prose has no aspect',
    );
  });
});

describe('board paths', () => {
  test('uses the language code under the aspect, and the short name without one', () => {
    expect(boardSlug({ ...votes(true), language: 'zh' })).toBe('zh');
    expect(boardSlug(votes(true, 'Smoke Tasks'))).toBe('smoke-tasks');
  });

  test('builds a board and a system path from the aspect and the slug', () => {
    const [family] = listFamilies(
      [{ name: 'prose-zh', config: { ...votes(true, 'Chinese'), language: 'zh' } }],
      [],
      FIXTURE_NAMES,
    );

    expect(boardPath(family)).toBe('/technical-writing/zh/');
    expect(systemPath(family, 'acme-pro')).toBe('/technical-writing/zh/acme-pro/');
  });

  test('redirects the old family path to the board, at any depth', () => {
    const [family] = listFamilies(
      [{ name: 'prose-zh', config: { ...votes(true, 'Chinese'), language: 'zh' } }],
      [],
      FIXTURE_NAMES,
    );

    expect(redirectLines([family])).toBe(
      [
        '/prose-zh /technical-writing/zh/ 301',
        '/prose-zh/ /technical-writing/zh/ 301',
        '/prose-zh/* /technical-writing/zh/:splat 301',
        '',
      ].join('\n'),
    );
  });

  test('rejects two public families sharing a board path', () => {
    const configs = [
      { name: 'prose-zh', config: { ...votes(true, 'Chinese'), language: 'zh' } },
      { name: 'prose-zh-two', config: { ...votes(true, 'Chinese Two'), language: 'zh' } },
    ] satisfies { name: string; config: FamilyConfig }[];

    expect(() => listFamilies(configs, [], FIXTURE_NAMES)).toThrow(
      'families prose-zh and prose-zh-two share the board path /technical-writing/zh/',
    );
  });
});

describe('systemPages', () => {
  test('gives a page to every system a voted round placed', async () => {
    const round = await report('prose', '2026-10-21', '2026-10-21T18:00:00Z');
    const [family] = listFamilies([{ name: 'prose', config: votes(true) }], [round], FIXTURE_NAMES);

    const pages = systemPages(family);

    expect([...pages].toSorted()).toEqual(round.results.standings.map((s) => s.system).toSorted());
  });
});
