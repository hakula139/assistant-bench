import { describe, expect, test } from 'vitest';

import { logTicks } from '@/components/charts/ticks';

describe('logTicks', () => {
  test.each<[string, [number, number], number, number[]]>([
    ['1, 2, and 5 per decade when they fit', [0.004, 0.06], 6, [0.005, 0.01, 0.02, 0.05]],
    ['fewer ticks per decade when the axis is narrow', [0.5, 400], 4, [1, 10, 100]],
    ['every few powers of ten when one per decade is too many', [0.0002, 20], 4, [0.01, 1]],
    ['linear ticks within one short span', [20.1, 48.2], 4, [30, 40]],
    ['linear ticks including a free metric', [0, 1], 4, [0, 0.5, 1]],
    ['one tick when every metric is zero', [0, 0], 4, [0]],
  ])('marks %s', (_, domain, count, ticks) => {
    const values = logTicks(domain, count);

    expect(values).toEqual(ticks);
  });
});
