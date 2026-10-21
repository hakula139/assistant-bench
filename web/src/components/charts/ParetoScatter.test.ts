import { describe, expect, test } from 'vitest';

import { paretoFrontier, type ScatterPoint } from '@/components/charts/ParetoScatter';

const point = (system: string, x: number, score: number): ScatterPoint => ({
  system,
  x,
  score,
  lower: score - 10,
  upper: score + 10,
});

describe('paretoFrontier', () => {
  test('keeps the points no other point beats on both axes, cheapest first', () => {
    const points = [
      point('best', 4, 1000),
      point('dominated', 3, 900),
      point('cheap', 1, 850),
      point('pricier-same-score', 2, 850),
      point('mid', 2, 950),
    ];

    const frontier = paretoFrontier(points);

    expect(frontier.map((item) => item.system)).toEqual(['cheap', 'mid', 'best']);
  });

  test('keeps points tied on both axes', () => {
    const points = [point('first', 1, 900), point('second', 1, 900), point('worse', 1, 850)];

    const frontier = paretoFrontier(points);

    expect(frontier.map((item) => item.system).toSorted()).toEqual(['first', 'second']);
  });
});
