import { describe, expect, test } from 'vitest';

import { familyConfigSchema, type FamilyConfig, type VotesConfig } from '@/lib/family';
import { unmeasuredRows } from '@/lib/forest';
import { reportSchema, type Report } from '@/lib/report';
import {
  boardSlug,
  currentReport,
  publicFamilies,
  roundLinks,
  roundPaths,
  systemPages,
  type VotesFamily,
} from '@/lib/reports';
import { FIXTURE_NAMES, readFixture, readReport } from '@/test/fixture';

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
    ) as VotesFamily[];
    const { rounds } = family;

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

  test('keeps a same-day closed round distinct from the released board', async () => {
    const name = 'writing-prose';
    const closed = await report(name, '2026-10-07', '2026-10-07T18:00:00Z');
    const released = { ...closed, closed_at: '2026-10-07T23:00:00Z' };
    const [family] = publicFamilies(
      [{ name, config: votes(true) }],
      [closed],
      {},
      {
        [name]: released,
      },
    );

    expect(currentReport(family)).toBe(released);
    expect(roundPaths([family]).map(({ props }) => props.round)).toEqual([closed]);
    expect(roundLinks(family, closed.round)).toEqual([
      { href: '/writing/prose/', label: 'Current', current: false },
      { href: '/writing/prose/rounds/2026-10-07/', label: closed.round, current: true },
    ]);
    expect(roundLinks(family).map(({ current }) => current)).toEqual([true, false]);
  });

  test('keeps closed rounds when the released board has no scores', async () => {
    const name = 'writing-prose';
    const closed = await report(name, '2026-10-07', '2026-10-07T18:00:00Z');
    const [family] = publicFamilies(
      [{ name, config: votes(true) }],
      [closed],
      {},
      {
        [name]: null,
      },
    );

    expect(currentReport(family)).toBeUndefined();
    expect(roundPaths([family]).map(({ props }) => props.round)).toEqual([closed]);
  });

  test('rejects a release keyed under another family or for a private family', async () => {
    const name = 'writing-prose';
    const closed = await report('another-family', '2026-10-07', '2026-10-07T18:00:00Z');

    expect(() =>
      publicFamilies([{ name, config: votes(true) }], [], {}, { [name]: closed }),
    ).toThrow(`current report another-family is keyed as ${name}`);
    expect(() =>
      publicFamilies([{ name, config: votes(false) }], [], {}, { [name]: null }),
    ).toThrow(`current report needs a public voted family: ${name}`);
  });

  test("refuses a report that another scorer than its family's wrote", async () => {
    const closed = { ...(await readReport('report-tests', 'tests')), family: 'writing-prose' };

    expect(() =>
      publicFamilies([{ name: 'writing-prose', config: votes(true) }], [closed], {}),
    ).toThrow(`report writing-prose/${closed.round} is scored by tests, not votes`);
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
        scorer: 'votes',
        config: votes(true),
        rounds: [],
        systemNames: FIXTURE_NAMES,
      },
    ]);
  });

  test('uses family-local alias labels in unmeasured rows', () => {
    const config = familyConfigSchema.parse({
      ...votes(true),
      systems: ['acme-large', 'acme-large-custom'],
      system_aliases: {
        'acme-large-custom': {
          model_effort: 'acme-large',
          condition: 'prompted',
          display_name: 'Acme Large with Instructions',
        },
      },
    });

    const [english, chinese] = publicFamilies(
      [
        { name: 'writing-en', config },
        { name: 'writing-zh', config: votes(true, 'Chinese Prose') },
      ],
      [],
      FIXTURE_NAMES,
    );

    expect(unmeasuredRows(english.config.systems, english.systemNames)).toEqual([
      { system: 'acme-large', name: 'Acme Large', unplaced: 'no-votes' },
      { system: 'acme-large-custom', name: 'Acme Large with Instructions', unplaced: 'no-votes' },
    ]);
    expect(chinese.systemNames).toEqual(FIXTURE_NAMES);
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
    ) as VotesFamily[];

    const pages = systemPages(family);

    expect([...pages].toSorted()).toEqual(round.results.standings.map((s) => s.system).toSorted());
  });
});
