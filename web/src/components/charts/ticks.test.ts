import { describe, expect, test } from 'vitest';

import { logTicks } from '@/components/charts/ticks';

describe('logTicks', () => {
  test('marks 1, 2, and 5 per decade when they fit', () => {
    const values = logTicks([0.004, 0.06], 6);

    expect(values).toEqual([0.005, 0.01, 0.02, 0.05]);
  });

  test('thins to fewer ticks per decade when the axis is narrow', () => {
    const values = logTicks([0.5, 400], 4);

    expect(values).toEqual([1, 10, 100]);
  });

  test('falls back to linear ticks within one short span', () => {
    const values = logTicks([20.1, 48.2], 4);

    expect(values).toEqual([30, 40]);
  });
});
