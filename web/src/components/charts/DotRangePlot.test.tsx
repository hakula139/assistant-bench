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

  test("draws the emphasized row's dot and range in the emphasis color", async () => {
    const rows = [
      { label: 'acme-pro', value: 0.6, range: { lower: 0.5, upper: 0.7 } },
      { label: 'acme-large', value: 0.4, range: { lower: 0.3, upper: 0.5 }, emphasis: true },
    ];

    const { container } = render(<DotRangePlot rows={rows} label="Win rate" format="percent" />);
    const svg = await vi.waitFor(() => {
      const drawn = container.querySelector('svg');
      expect(drawn).not.toBeNull();
      return drawn as SVGSVGElement;
    });

    const colors = (selector: string, attribute: string) =>
      [...svg.querySelectorAll(selector)].map((node) => node.getAttribute(attribute));

    expect(colors('g[aria-label="dot"] circle', 'fill')).toEqual([
      'var(--chart-series)',
      'var(--chart-emphasis)',
    ]);
    expect(colors('g[aria-label="rule"] line', 'stroke')).toEqual([
      'var(--chart-series)',
      'var(--chart-emphasis)',
    ]);
  });
});
