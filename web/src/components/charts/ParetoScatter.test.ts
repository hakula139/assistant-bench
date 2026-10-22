import { describe, expect, test } from 'vitest';

import { paretoFrontier, placeLabels, type ScatterPoint } from '@/components/charts/ParetoScatter';

const point = (system: string, x: number, score: number): ScatterPoint => ({
  system,
  name: system,
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

describe('placeLabels', () => {
  test("moves a label off another point's interval whisker", () => {
    const leader = point('leader', 100, 100);
    const below = { ...point('below', 60, 105), lower: 80, upper: 130 };

    const placements = placeLabels(
      [leader],
      [leader, below],
      (x, score) => [x, score],
      () => 50,
      { left: 0, right: 300, top: 0, bottom: 300 },
    );

    expect(placements.get(leader)).toMatchObject({ textAnchor: 'start', dy: -11 });
  });
});
