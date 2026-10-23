import { describe, expect, test } from 'vitest';

import {
  EMPTY_DOMAIN,
  clip,
  forestDomain,
  forestTicks,
  labelledTicks,
  legendMarks,
  roundRows,
  sharedDomain,
  sparseTicks,
  unionMarks,
  unmeasuredRows,
  type Clipped,
  type Estimate,
  type ForestRow,
  type LegendMark,
} from '@/lib/forest';
import { reportSchema } from '@/lib/report';
import { FIXTURE_NAMES, readFixture } from '@/test/fixture';

const estimate = (score: number, lower: number, upper: number) => ({
  score,
  interval: { lower, upper },
});

describe('forestDomain', () => {
  test.each<[string, Estimate[], [number, number]]>([
    [
      'an axis over every interval, rounded out to round ticks',
      [estimate(1004, 955, 1065), estimate(825, 773, 890)],
      [750, 1100],
    ],
    [
      'an axis that starts no lower than the span below the top score',
      [estimate(1004, 955, 1065), estimate(233, 81, 321)],
      [400, 1100],
    ],
    ['the empty axis without estimates', [], EMPTY_DOMAIN],
  ])('returns %s', (_, estimates, expected) => {
    const domain = forestDomain(estimates);

    expect(domain).toEqual(expected);
  });
});

describe('sharedDomain', () => {
  test.each<[string, Estimate[][], [number, number]]>([
    [
      "an axis that cuts each board below its own top score, not the highest board's",
      [
        [estimate(1400, 1300, 1500), estimate(1000, 950, 1050)],
        [estimate(1000, 950, 1050), estimate(200, 100, 300)],
      ],
      [400, 1500],
    ],
    [
      'the axis of the boards with estimates alone',
      [[], [estimate(1004, 955, 1065), estimate(825, 773, 890)]],
      [750, 1100],
    ],
  ])('returns %s', (_, boards, expected) => {
    const domain = sharedDomain(boards);

    expect(domain).toEqual(expected);
  });
});

describe('forestTicks', () => {
  test('keeps both ends of an axis that sits off the round step', () => {
    expect(forestTicks([400, 2500])).toEqual([400, 1000, 1500, 2000, 2500]);
  });
});

describe('sparseTicks', () => {
  test.each<[string, [number, number], number[]]>([
    ['both ends and an anchor far from them', [600, 1400], [600, 1000, 1400]],
    ['both ends alone when the anchor sits next to one', [400, 1100], [400, 1100]],
  ])('labels %s', (_, domain, ticks) => {
    expect(sparseTicks(domain)).toEqual(ticks);
  });
});

describe('labelledTicks', () => {
  test('labels only the sparse ticks below 240 px and every tick from there', () => {
    const domain: [number, number] = [600, 1400];

    const ticks = [labelledTicks(domain, 240), labelledTicks(domain, 239)];

    expect(ticks).toEqual([forestTicks(domain), [600, 1000, 1400]]);
  });
});

describe('clip', () => {
  test.each<[string, Estimate, [number, number], Clipped]>([
    [
      'a score below the axis to its start and marks the cut',
      estimate(233, 81, 321),
      [400, 1100],
      { score: 400, lower: 400, upper: 400, offScale: true, cutLow: true },
    ],
    [
      'only the end of an interval that crosses the axis start',
      estimate(666, 441, 796),
      [500, 1300],
      { score: 666, lower: 500, upper: 796, offScale: false, cutLow: true },
    ],
  ])('clips %s', (_, value, domain, expected) => {
    const clipped = clip(value, domain);

    expect(clipped).toEqual(expected);
  });
});

describe('roundRows', () => {
  test('orders the standings by score and links them, then the unranked and those without votes by name', async () => {
    const report = reportSchema.parse(await readFixture());
    if (report.results.scorer !== 'votes') {
      throw new Error('the fixture is a votes report');
    }
    const { standings, unranked } = report.results;

    const rows = roundRows(
      (system) => `/board/${system}/`,
      ['acme-new', 'acme-large', 'acme-gone'],
      { standings: standings.toReversed(), unranked: unranked.toReversed() },
      { ...FIXTURE_NAMES, 'acme-gone': 'Acme Gone' },
    );

    expect(rows.map((row) => [row.system, 'unplaced' in row ? row.unplaced : row.href])).toEqual([
      ['acme-pro', '/board/acme-pro/'],
      ['acme-large', '/board/acme-large/'],
      ['acme-small', '/board/acme-small/'],
      ['acme-flash', '/board/acme-flash/'],
      ['acme-mini', '/board/acme-mini/'],
      ['acme-tiny', '/board/acme-tiny/'],
      ['acme-edge', 'unranked'],
      ['acme-nano', 'unranked'],
      ['acme-gone', 'no-votes'],
      ['acme-new', 'no-votes'],
    ]);
  });
});

describe('unmeasuredRows', () => {
  test('labels each row by display name and orders the rows by it', () => {
    const names = { 'acme-a': 'Zenith', 'acme-b': 'Apex' };

    const rows = unmeasuredRows(['acme-a', 'acme-b'], 'acme-a', names);

    expect(rows.map((row) => [row.system, row.name])).toEqual([
      ['acme-b', 'Apex'],
      ['acme-a', 'Zenith'],
    ]);
  });
});

describe('legendMarks', () => {
  const domain: [number, number] = [400, 1100];
  const row = (score: number, lower: number, upper: number, preliminary = false): ForestRow => ({
    system: 'acme-pro',
    name: 'Acme Pro',
    estimate: estimate(score, lower, upper),
    preliminary,
  });

  test.each<[string, ForestRow, boolean, LegendMark[]]>([
    ['a settled score and the anchor rule', row(1004, 955, 1065), true, ['score', 'anchor']],
    [
      'a preliminary score apart from a settled one',
      row(700, 600, 800, true),
      false,
      ['preliminary'],
    ],
    [
      'the cut when an interval runs past the axis start',
      row(500, 300, 700),
      false,
      ['score', 'cut'],
    ],
    ['the cut when a score runs past the axis start', row(350, 250, 450), false, ['score', 'cut']],
    [
      'the anchor dot without an unbounded track',
      { system: 'acme-large', name: 'Acme Large', unplaced: 'anchor' },
      true,
      ['anchor'],
    ],
    [
      'the unbounded track of an unranked system',
      { system: 'acme-edge', name: 'Acme Edge', unplaced: 'unranked' },
      false,
      ['unbounded'],
    ],
  ])('names %s', (_, drawn, anchorLine, marks) => {
    expect(legendMarks([drawn], domain, anchorLine)).toEqual(marks);
  });
});

describe('unionMarks', () => {
  test("keeps every plot's marks once, in legend order", () => {
    expect(unionMarks([['unbounded', 'anchor'], ['score', 'anchor'], []])).toEqual([
      'score',
      'anchor',
      'unbounded',
    ]);
  });
});
