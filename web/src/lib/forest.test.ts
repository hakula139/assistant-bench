import { describe, expect, test } from 'vitest';

import { EMPTY_DOMAIN, clip, forestDomain } from '@/lib/forest';

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

describe('clip', () => {
  test('moves a score below the axis to its start and marks the cut', () => {
    const clipped = clip(estimate(233, 81, 321), [400, 1100]);

    expect(clipped).toEqual({
      score: 400,
      lower: 400,
      upper: 400,
      offScale: true,
      cutLow: true,
      cutHigh: false,
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
      cutHigh: false,
    });
  });
});
