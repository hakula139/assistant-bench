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
  type ForestRow,
} from '@/lib/forest';
import { reportSchema } from '@/lib/report';
import { FIXTURE_NAMES, readFixture } from '@/test/fixture';

const estimate = (score: number, lower: number, upper: number) => ({
  score,
  interval: { lower, upper },
});

describe('forestDomain', () => {
  test('covers every interval, rounded out to round ticks', () => {
    const domain = forestDomain([estimate(1004, 955, 1065), estimate(825, 773, 890)]);

    expect(domain).toEqual([750, 1100]);
  });

  test('starts no lower than the span below the top score', () => {
    const domain = forestDomain([estimate(1004, 955, 1065), estimate(233, 81, 321)]);

    expect(domain).toEqual([400, 1100]);
  });

  test('falls back to the empty axis without estimates', () => {
    expect(forestDomain([])).toEqual(EMPTY_DOMAIN);
  });
});

describe('sharedDomain', () => {
  test("cuts each board below its own top score, not the highest board's", () => {
    const strong = [estimate(1400, 1300, 1500), estimate(1000, 950, 1050)];
    const weak = [estimate(1000, 950, 1050), estimate(200, 100, 300)];

    const domain = sharedDomain([strong, weak]);

    expect(domain).toEqual([400, 1500]);
  });

  test('ignores boards without estimates', () => {
    const domain = sharedDomain([[], [estimate(1004, 955, 1065), estimate(825, 773, 890)]]);

    expect(domain).toEqual([750, 1100]);
  });
});

describe('forestTicks', () => {
  test('keeps both ends of an axis that sits off the round step', () => {
    expect(forestTicks([400, 2500])).toEqual([400, 1000, 1500, 2000, 2500]);
  });
});

describe('sparseTicks', () => {
  test('labels both ends and a reference far from them', () => {
    expect(sparseTicks([600, 1400])).toEqual([600, 1000, 1400]);
  });

  test('leaves out a reference next to an end', () => {
    expect(sparseTicks([400, 1100])).toEqual([400, 1100]);
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
  test('moves a score below the axis to its start and marks the cut', () => {
    const clipped = clip(estimate(233, 81, 321), [400, 1100]);

    expect(clipped).toEqual({
      score: 400,
      lower: 400,
      upper: 400,
      offScale: true,
      cutLow: true,
    });
  });

  test('cuts only the end of an interval that crosses the axis start', () => {
    const clipped = clip(estimate(666, 441, 796), [500, 1300]);

    expect(clipped).toEqual({
      score: 666,
      lower: 500,
      upper: 796,
      offScale: false,
      cutLow: true,
    });
  });
});

describe('roundRows', () => {
  test('orders the standings by score, then the unranked and those without votes by name', async () => {
    const report = reportSchema.parse(await readFixture());
    if (report.results.scorer !== 'votes') {
      throw new Error('the fixture is a votes report');
    }
    const { standings, unranked } = report.results;

    const rows = roundRows(
      'arena',
      ['acme-new', 'acme-large', 'acme-gone'],
      { standings: standings.toReversed(), unranked: unranked.toReversed() },
      { ...FIXTURE_NAMES, 'acme-gone': 'Acme Gone' },
    );

    expect(rows.map((row) => [row.system, 'unplaced' in row ? row.unplaced : 'placed'])).toEqual([
      ['acme-pro', 'placed'],
      ['acme-large', 'placed'],
      ['acme-small', 'placed'],
      ['acme-flash', 'placed'],
      ['acme-mini', 'placed'],
      ['acme-tiny', 'placed'],
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

  test('names a settled score and the reference rule', () => {
    expect(legendMarks([row(1004, 955, 1065)], domain, true)).toEqual(['score', 'reference']);
  });

  test('names a preliminary score apart from a settled one', () => {
    expect(legendMarks([row(700, 600, 800, true)], domain, false)).toEqual(['preliminary']);
  });

  test.each([
    ['an interval', row(500, 300, 700)],
    ['a score', row(350, 250, 450)],
  ])('names the cut when %s runs past the axis start', (_, cut) => {
    expect(legendMarks([cut], domain, false)).toEqual(['score', 'cut']);
  });

  test('names the unbounded track for unplaced systems but not for the reference dot', () => {
    const reference: ForestRow = {
      system: 'acme-large',
      name: 'Acme Large',
      unplaced: 'reference',
    };
    const unranked: ForestRow = { system: 'acme-edge', name: 'Acme Edge', unplaced: 'unranked' };

    expect(legendMarks([reference], domain, true)).toEqual(['reference']);
    expect(legendMarks([unranked], domain, false)).toEqual(['unbounded']);
  });
});

describe('unionMarks', () => {
  test("keeps every plot's marks once, in legend order", () => {
    expect(unionMarks([['unbounded', 'reference'], ['score', 'reference'], []])).toEqual([
      'score',
      'reference',
      'unbounded',
    ]);
  });
});
