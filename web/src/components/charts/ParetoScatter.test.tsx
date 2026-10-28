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

    expect(placements.get(leader)).toEqual({ placement: { dx: 7, dy: -11, textAnchor: 'start' } });
  });

  test('moves a label off a spot nearer another dot, though it clears that dot', () => {
    const small = point('small', 100, 100);
    const pro = point('pro', 98, 89);

    const placements = placeLabels(
      [small],
      [small, pro],
      (x, score) => [x, score],
      () => 50,
      { left: 0, right: 300, top: 0, bottom: 300 },
    );

    expect(placements.get(small)).toEqual({ placement: { dx: 7, dy: -11, textAnchor: 'start' } });
  });

  test('takes the centered spot above where every corner sits nearer another dot', () => {
    const crowded = point('crowded', 100, 100);
    const neighbors = [
      point('upper-left', 68, 89),
      point('upper-right', 132, 89),
      point('lower-right', 132, 113),
      point('lower-left', 68, 113),
    ];

    const placements = placeLabels(
      [crowded],
      [crowded, ...neighbors],
      (x, score) => [x, score],
      () => 50,
      { left: 0, right: 300, top: 0, bottom: 300 },
    );

    expect(placements.get(crowded)).toEqual({
      placement: { dx: 0, dy: -13, textAnchor: 'middle' },
    });
  });

  test('ties a label on the far ring to its dot with a leader that passes no other dot', () => {
    const boxed = point('boxed', 100, 100);
    const neighbors = [
      point('left', 82, 100),
      point('right', 118, 100),
      point('above', 100, 84),
      point('below', 100, 118),
      point('on-the-way', 89.5, 91),
    ];

    const placements = placeLabels(
      [boxed],
      [boxed, ...neighbors],
      (x, score) => [x, score],
      () => 20,
      { left: 0, right: 300, top: 0, bottom: 300 },
    );

    expect(placements.get(boxed)).toEqual({
      placement: { dx: 21, dy: -25, textAnchor: 'start' },
      leader: [121, 82],
    });
  });

  test('leaves a label off where no spot on either ring fits', () => {
    const cornered = point('cornered', 10, 10);

    const placements = placeLabels(
      [cornered],
      [cornered],
      (x, score) => [x, score],
      () => 50,
      { left: 0, right: 20, top: 0, bottom: 20 },
    );

    expect(placements.has(cornered)).toBe(false);
  });

  // Dot positions and label widths from the latest synthetic round's scatters at a 390 px viewport.
  test.each([
    {
      chart: 'cost',
      frontier: [
        point('DeepSeek V4.1 Flash 1017 · High', 61, 262.7),
        point('Gemini 3.8 Flash · High', 119.4, 152),
        point('Kimi K3 · High', 154.7, 50.6),
        point('Gemini 4 Argon · High', 239.6, 48),
      ],
      others: [
        point('Claude Opus 5.5 · High', 326, 48.5),
        point('a', 305.1, 164.2),
        point('b', 267.6, 252.5),
      ],
      expected: {
        'DeepSeek V4.1 Flash 1017 · High': { placement: { dx: 7, dy: -11, textAnchor: 'start' } },
        'Gemini 3.8 Flash · High': { placement: { dx: 7, dy: 13, textAnchor: 'start' } },
        'Kimi K3 · High': { placement: { dx: -7, dy: -11, textAnchor: 'end' } },
        'Gemini 4 Argon · High': { placement: { dx: 0, dy: -13, textAnchor: 'middle' } },
      },
    },
    {
      chart: 'latency',
      frontier: [
        point('Gemini 3.8 Flash · High', 61, 152),
        point('Kimi K3 · High', 75.2, 50.6),
        point('Gemini 4 Argon · High', 188.3, 48),
      ],
      others: [
        point('Claude Opus 5.5 · High', 256.9, 48.5),
        point('a', 326, 164.2),
        point('b', 102, 252.5),
        point('c', 154.9, 262.7),
      ],
      expected: {
        'Gemini 3.8 Flash · High': { placement: { dx: 7, dy: 13, textAnchor: 'start' } },
        'Kimi K3 · High': { placement: { dx: 7, dy: -11, textAnchor: 'start' } },
        'Gemini 4 Argon · High': { placement: { dx: 0, dy: 15, textAnchor: 'middle' } },
      },
    },
  ])(
    'keeps every label beside its own dot on the narrow $chart scatter',
    ({ frontier, others, expected }) => {
      const widths: Record<string, number> = {
        'DeepSeek V4.1 Flash 1017 · High': 180.2,
        'Gemini 3.8 Flash · High': 124.8,
        'Kimi K3 · High': 75.9,
        'Gemini 4 Argon · High': 116.9,
      };
      const placements = placeLabels(
        frontier,
        [...frontier, ...others],
        (x, score) => [x, score],
        (text) => widths[text.replace(/^Name /, '')],
        { left: 45, right: 358, top: 0, bottom: 280 },
      );

      expect(
        Object.fromEntries([...placements].map(([item, label]) => [item.system, label])),
      ).toEqual(expected);
    },
  );
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
  ])(
    'fits %s score labels and the axis label flush in the left margin it measures',
    async (_, points) => {
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

      const axisLabel = svg.querySelector('g[aria-label="y-axis label"]');
      const axisLabelLeft = offset(axisLabel) + offset(axisLabel?.querySelector('text') ?? null);

      expect(lefts).not.toEqual([]);
      expect(Math.min(...lefts)).toBeGreaterThanOrEqual(0);
      expect(Math.min(...lefts)).toBeLessThan(1);
      expect(axisLabelLeft).toBeGreaterThanOrEqual(0);
      expect(axisLabelLeft).toBeLessThan(1);
    },
  );

  test('labels both ends of a narrow axis where one round tick falls inside it', async () => {
    stubPlotFrame(288);
    const points = [point('quick', 11, 900), point('slow', 29, 1000)];

    const { container } = render(
      <ParetoScatter points={points} label="Latency" format={(value) => `${value} seconds`} />,
    );
    const svg = await vi.waitFor(() => {
      const found = container.querySelector('svg');
      expect(found).not.toBeNull();
      return found as SVGSVGElement;
    });

    const ticks = [...svg.querySelectorAll('g[aria-label="x-axis tick label"] text')].map(
      (node) => node.textContent,
    );

    expect(ticks).toEqual(['11 seconds', '29 seconds']);
  });

  test('draws a leader from a boxed-in dot to the nearest corner of its far label', async () => {
    const boxed = point('boxed', 50, 600);
    const points = [
      point('free', 0, 100),
      boxed,
      point('left', 46.4, 599),
      point('right', 53.6, 600),
      point('above', 50.01, 667),
      point('below', 50, 525),
      point('top', 100, 1000),
    ];

    const { container } = render(
      <ParetoScatter points={points} label="Price" format={(value) => `$${value}`} />,
    );
    const svg = await vi.waitFor(() => {
      const found = container.querySelector('svg');
      expect(found).not.toBeNull();
      return found as SVGSVGElement;
    });

    const dot = svg.querySelectorAll('g[aria-label="dot"] circle')[points.indexOf(boxed)];
    const [cx, cy] = [Number(dot.getAttribute('cx')), Number(dot.getAttribute('cy'))];
    const ends = [
      ...(svg.querySelector('g[aria-label="link"] path')?.getAttribute('d') ?? '').matchAll(
        /-?[\d.]+/g,
      ),
    ].map(Number);
    const label = [...svg.querySelectorAll('g[aria-label="text"] text')].find(
      (node) => node.textContent === boxed.name,
    );

    // The far upper-left label ends 21 pixels left of the dot, and its bottom sits 18 above it.
    expect(ends.map((value) => value.toFixed(1))).toEqual(
      [cx, cy, cx - 21, cy - 18].map((value) => value.toFixed(1)),
    );
    expect(label?.parentElement?.getAttribute('transform')).toBe('translate(-20.5,-24.5)');
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
