import { describe, expect, test } from 'vitest';

import {
  EMPTY_DOMAIN,
  clip,
  forestDomain,
  roundRows,
  sharedDomain,
  sparseTicks,
} from '@/lib/forest';
import { reportSchema } from '@/lib/report';
import { readFixture } from '@/test/fixture';

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
    const weak = [estimate(1000, 950, 1050), estimate(750, 700, 800)];

    const [start, stop] = sharedDomain([strong, weak]);

    expect(start).toBeLessThanOrEqual(700);
    expect(stop).toBeGreaterThanOrEqual(1500);
  });

  test('ignores boards without estimates', () => {
    const domain = sharedDomain([[], [estimate(1004, 955, 1065), estimate(825, 773, 890)]]);

    expect(domain).toEqual([750, 1100]);
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
  test('follows the standings with the unranked systems, then those without votes', async () => {
    const report = reportSchema.parse(await readFixture());
    if (report.results.scorer !== 'votes') {
      throw new Error('the fixture is a votes report');
    }

    const rows = roundRows('arena', ['acme-large', 'acme-new'], report.results);

    expect(rows.map((row) => [row.system, 'unplaced' in row ? row.unplaced : 'placed'])).toEqual([
      ['acme-pro', 'placed'],
      ['acme-large', 'placed'],
      ['acme-small', 'placed'],
      ['acme-flash', 'placed'],
      ['acme-mini', 'placed'],
      ['acme-tiny', 'placed'],
      ['acme-edge', 'unranked'],
      ['acme-nano', 'unranked'],
      ['acme-new', 'no-votes'],
    ]);
  });
});
