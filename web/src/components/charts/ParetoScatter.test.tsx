// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import {
  paretoFrontier,
  ParetoScatter,
  placeLabels,
  type ScatterPoint,
} from '@/components/charts/ParetoScatter';
import { stubPlotFrame } from '@/test/plot';

const point = (system: string, x: number, score: number): ScatterPoint => ({
  system,
  name: `Name ${system}`,
  x,
  score,
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
  test('moves a label off another point', () => {
    const leader = point('leader', 100, 100);
    const below = point('below', 60, 89);

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

describe('ParetoScatter', () => {
  beforeEach(() => {
    stubPlotFrame();
  });

  afterEach(cleanup);

  test.each([
    ['mixed free and paid systems', [point('free', 0, 900), point('paid', 1, 1000)]],
    ['only free systems', [point('free', 0, 900), point('best-free', 0, 1000)]],
  ])('draws finite positions for %s and keeps their zero-cost points', async (_, points) => {
    const { container } = render(
      <ParetoScatter points={points} label="Price" format={(value) => `$${value}`} />,
    );

    const svg = await vi.waitFor(() => {
      const found = container.querySelector('svg');
      expect(found).not.toBeNull();
      return found as SVGSVGElement;
    });
    const positions = [...svg.querySelectorAll('g[aria-label="dot"] circle')].map((dot) => [
      Number(dot.getAttribute('cx')),
      Number(dot.getAttribute('cy')),
    ]);
    const labels = [...svg.querySelectorAll('g[aria-label="text"] text')].map(
      (node) => node.textContent,
    );

    expect(positions).toHaveLength(points.length);
    expect(positions.flat().every(Number.isFinite)).toBe(true);
    expect(positions.every(([x]) => x >= 0 && x <= 600)).toBe(true);
    expect(labels.toSorted()).toEqual(
      paretoFrontier(points)
        .map((item) => item.name)
        .toSorted(),
    );
    expect(svg.textContent).toContain('$0');
    expect(svg.outerHTML).not.toMatch(/NaN|Infinity/);
  });

  test.each([
    ['three-digit', [point('low', 1, 400), point('high', 2, 900)]],
    ['five-digit', [point('low', 1, 9000), point('high', 2, 12000)]],
  ])('fits %s score labels in the left margin it measures', async (_, points) => {
    const { container } = render(
      <ParetoScatter points={points} label="Price" format={(value) => `$${value}`} />,
    );
    const svg = await vi.waitFor(() => {
      const found = container.querySelector('svg');
      expect(found).not.toBeNull();
      return found as SVGSVGElement;
    });

    const group = svg.querySelector('g[aria-label="y-axis tick label"]');
    const offset = (element: Element | null) =>
      Number(/translate\((-?[\d.]+)/.exec(element?.getAttribute('transform') ?? '')?.[1]);
    // The stubbed frame measures seven pixels per character, and the labels end at their x.
    const lefts = [...(group?.querySelectorAll('text') ?? [])].map(
      (label) => offset(group) + offset(label) - label.textContent.length * 7,
    );

    expect(lefts).not.toEqual([]);
    expect(Math.min(...lefts)).toBeGreaterThanOrEqual(0);
    expect(Math.min(...lefts)).toBeLessThan(1);
  });

  test('labels the frontier and its tips by display name', async () => {
    const points = [point('cheap', 1, 900), point('best', 4, 1000), point('dominated', 3, 850)];

    const { container } = render(
      <ParetoScatter points={points} label="Price" format={(value) => `$${value}`} />,
    );
    const svg = await vi.waitFor(() => {
      const found = container.querySelector('svg');
      expect(found).not.toBeNull();
      return found as SVGSVGElement;
    });
    const cheap = svg.querySelector('g[aria-label="dot"] circle');

    svg.dispatchEvent(
      new PointerEvent('pointermove', {
        pointerType: 'mouse',
        clientX: Number(cheap?.getAttribute('cx')),
        clientY: Number(cheap?.getAttribute('cy')),
      }),
    );

    const labels = [...svg.querySelectorAll('g[aria-label="text"] text')].map(
      (node) => node.textContent,
    );
    const tip = svg.querySelector('g[aria-label="tip"] tspan')?.textContent;

    expect(labels.toSorted()).toEqual(['Name best', 'Name cheap']);
    // Plot starts each tip line with a zero-width space.
    expect(tip).toBe('\u200bName cheap');
  });
});
