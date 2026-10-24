import { describe, expect, test } from 'vitest';

import {
  clip,
  EMPTY_DOMAIN,
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
import { formatScore } from '@/lib/format';
import { reportSchema } from '@/lib/report';
import { reportNames } from '@/lib/systems';
import { readFixture } from '@/test/fixture';

const estimate = (score: number) => ({ score });

describe('forestDomain', () => {
  test.each<[string, Estimate[], [number, number]]>([
    [
      'an axis over every point, rounded out to round ticks',
      [estimate(1004), estimate(825)],
      [800, 1050],
    ],
    [
      'an axis that starts no lower than the span below the top score',
      [estimate(1004), estimate(233)],
      [400, 1100],
    ],
    ['the empty axis without estimates', [], EMPTY_DOMAIN],
  ])('returns %s', (_, estimates, expected) => {
    const domain = forestDomain(estimates);

    expect(domain).toEqual(expected);
  });

  test.each<[string, Estimate[]]>([
    ['an all-tie round', [estimate(1000), estimate(1000)]],
    ['one standing', [estimate(1234)]],
  ])('keeps a finite display span around %s without altering its points', (_, estimates) => {
    const original = structuredClone(estimates);

    const domain = forestDomain(estimates);
    const ticks = forestTicks(domain);

    expect(domain.every(Number.isFinite)).toBe(true);
    expect(domain[0]).toBeLessThan(estimates[0].score);
    expect(domain[1]).toBeGreaterThan(estimates[0].score);
    expect(ticks.every(Number.isFinite)).toBe(true);
    expect(new Set(ticks).size).toBe(ticks.length);
    expect(estimates).toEqual(original);
  });

  test.each([1000, 0, -1000])(
    'keeps distinct whole-point labels around near ties at %s',
    (score) => {
      const estimates = [estimate(score), estimate(score + 0.0139186)];
      const original = structuredClone(estimates);

      const domain = forestDomain(estimates);
      const labels = forestTicks(domain).map(formatScore);

      expect(new Set(labels).size).toBe(labels.length);
      expect(domain[0]).toBeLessThanOrEqual(score);
      expect(domain[1]).toBeGreaterThanOrEqual(score + 0.0139186);
      expect(estimates).toEqual(original);
    },
  );
});

describe('sharedDomain', () => {
  test.each<[string, Estimate[][], [number, number]]>([
    [
      "an axis that cuts each board below its own top score, not the highest board's",
      [
        [estimate(1400), estimate(1000)],
        [estimate(1000), estimate(200)],
      ],
      [400, 1400],
    ],
    [
      'the axis of the boards with estimates alone',
      [[], [estimate(1004), estimate(825)]],
      [800, 1050],
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
    expect(sparseTicks(domain, 1000)).toEqual(ticks);
  });
});

describe('labelledTicks', () => {
  test.each<[[number, number], number, number[]]>([
    [[600, 1400], 160, [600, 1000, 1400]],
    [[400, 1100], 280, [400, 1100]],
  ])(
    'labels every tick of %j from %i px, 40 px an interval, and only the sparse ones below',
    (domain, width, sparse) => {
      const ticks = [labelledTicks(domain, width, 1000), labelledTicks(domain, width - 1, 1000)];

      expect(ticks).toEqual([forestTicks(domain), sparse]);
    },
  );
});

describe('clip', () => {
  test.each<[string, Estimate, [number, number], Clipped]>([
    [
      'a score below the axis to its start and marks the cut',
      estimate(233),
      [400, 1100],
      { score: 400, offScale: true },
    ],
    [
      'a point inside the axis without moving it',
      estimate(666),
      [500, 1300],
      { score: 666, offScale: false },
    ],
  ])('clips %s', (_, value, domain, expected) => {
    const clipped = clip(value, domain);

    expect(clipped).toEqual(expected);
  });
});

describe('roundRows', () => {
  test("orders and links measured standings, then only the report's unranked systems", async () => {
    const report = reportSchema.parse(await readFixture());
    if (report.results.scorer !== 'votes') {
      throw new Error('the fixture is a votes report');
    }
    const { standings, unranked } = report.results;

    const rows = roundRows(
      (system) => `/board/${system}/`,
      { standings: standings.toReversed(), unranked: unranked.toReversed() },
      reportNames(report.systems),
      report.results.rules,
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
  const row = (score: number, preliminary = false): ForestRow => ({
    system: 'acme-pro',
    name: 'Acme Pro',
    estimate: estimate(score),
    preliminary,
  });

  test.each<[string, ForestRow, boolean, LegendMark[]]>([
    ['a settled score and the anchor rule', row(1004), true, ['score', 'anchor']],
    ['a preliminary score apart from a settled one', row(700, true), false, ['preliminary']],
    ['a point inside the axis without a cut', row(500), false, ['score']],
    ['the cut when a score runs past the axis start', row(350), false, ['cut']],
    [
      'the anchor rule without a measured dot',
      { system: 'acme-large', name: 'Acme Large', unplaced: 'anchor' },
      true,
      ['anchor'],
    ],
    [
      'no measured mark for an unranked system',
      { system: 'acme-edge', name: 'Acme Edge', unplaced: 'unranked' },
      false,
      [],
    ],
  ])('names %s', (_, drawn, anchorLine, marks) => {
    expect(legendMarks([drawn], domain, anchorLine)).toEqual(marks);
  });
});

describe('unionMarks', () => {
  test("keeps every plot's marks once, in legend order", () => {
    expect(unionMarks([['preliminary', 'anchor'], ['score', 'anchor'], []])).toEqual([
      'score',
      'preliminary',
      'anchor',
    ]);
  });
});
