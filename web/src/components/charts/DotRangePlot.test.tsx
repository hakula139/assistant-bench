// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { DotRangePlot } from '@/components/charts/DotRangePlot';
import { stubPlotFrame } from '@/test/plot';

describe('DotRangePlot', () => {
  beforeEach(() => {
    stubPlotFrame();
  });

  afterEach(cleanup);

  test('draws ordered values, ranges and baseline with matching hover details and emphasis', async () => {
    const rows = [
      { label: 'acme-pro', value: 0.6, range: { lower: 0.5, upper: 0.7 } },
      { label: 'acme-large', value: 0.4, range: { lower: 0.3, upper: 0.5 }, emphasis: true },
      { label: 'acme-plain', value: 0.5 },
    ];

    const { container } = render(
      <DotRangePlot rows={rows} label="Win rate" format="percent" baseline={0.5} />,
    );
    const svg = await vi.waitFor(() => {
      const drawn = container.querySelector('svg');
      expect(drawn).not.toBeNull();
      return drawn as SVGSVGElement;
    });

    const dots = [...svg.querySelectorAll('g[aria-label="dot"] circle')];
    const [baseline, ...ranges] = [...svg.querySelectorAll('g[aria-label="rule"] line')];
    const positions = dots.map((dot) => ({
      x: Number(dot.getAttribute('cx')),
      y: Number(dot.getAttribute('cy')),
    }));
    const [pro, large, plain] = positions;
    const center = Number(baseline.getAttribute('x1'));

    expect(dots.map((dot) => dot.getAttribute('fill'))).toEqual([
      'var(--chart-series)',
      'var(--chart-emphasis)',
      'var(--chart-series)',
    ]);
    expect(ranges.map((range) => range.getAttribute('stroke'))).toEqual([
      'var(--chart-series)',
      'var(--chart-emphasis)',
    ]);
    expect(pro.y).toBeLessThan(large.y);
    expect(large.y).toBeLessThan(plain.y);
    expect(pro.x).toBeGreaterThan(center);
    expect(large.x).toBeLessThan(center);
    expect(pro.x - center).toBeCloseTo(center - large.x);
    expect(plain.x).toBeCloseTo(center);
    expect(Number(baseline.getAttribute('x2'))).toBeCloseTo(center);
    expect(Number(baseline.getAttribute('y1'))).toBeLessThan(pro.y);
    expect(Number(baseline.getAttribute('y2'))).toBeGreaterThan(plain.y);

    for (const [index, lower, upper] of [
      [0, center, 2 * pro.x - center],
      [1, 2 * large.x - center, center],
    ]) {
      const range = ranges[index];
      expect(Number(range.getAttribute('x1'))).toBeCloseTo(lower);
      expect(Number(range.getAttribute('x2'))).toBeCloseTo(upper);
      expect(Number(range.getAttribute('y1'))).toBeCloseTo(positions[index].y);
      expect(Number(range.getAttribute('y2'))).toBeCloseTo(positions[index].y);
    }

    for (const [index, lines] of [
      [0, ['acme-pro', '60% (50%–70%)']],
      [1, ['acme-large', '40% (30%–50%)']],
      [2, ['acme-plain', '50%']],
    ] as const) {
      svg.dispatchEvent(
        new PointerEvent('pointermove', {
          pointerType: 'mouse',
          clientX: positions[index].x,
          clientY: positions[index].y,
        }),
      );

      expect(
        [...svg.querySelectorAll('g[aria-label="tip"] tspan')].map((span) =>
          span.textContent.replaceAll('\u200b', ''),
        ),
      ).toEqual(lines);
    }
  });
});
