// @vitest-environment jsdom
import * as Plot from '@observablehq/plot';
import { cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { CategoryForests } from '@/components/charts/CategoryForests';
import { forestPlotOptions, type ForestPlotRow } from '@/components/charts/ForestPlot';
import type { CategoryBoard } from '@/lib/report';

const refit = (
  system: string,
  score: number,
  lower: number,
  upper: number,
  preliminary = false,
) => ({
  system,
  score,
  interval: { lower, upper },
  votes: 20,
  preliminary,
});

const frame = { width: 600, measure: (text: string) => text.length * 7 };

function draw(rows: ForestPlotRow[], domain: [number, number]): Element {
  const labels = rows.map((row) => row.label);
  return Plot.plot({ ...forestPlotOptions({ rows, labels, domain }, frame), width: frame.width });
}

/** The y positions of the interval rules, dashed or solid. */
function rules(svg: Element, dashed: boolean): number[] {
  return [...svg.querySelectorAll('g[aria-description="95% interval"]')]
    .filter((group) => group.hasAttribute('stroke-dasharray') === dashed)
    .flatMap((group) => [...group.querySelectorAll('line')])
    .map((line) => Number(line.getAttribute('y1')));
}

describe('forestPlotOptions', () => {
  test('dashes only the preliminary interval', () => {
    const svg = draw(
      [
        { label: 'settled', refit: refit('settled', 1000, 950, 1050) },
        { label: 'preliminary', refit: refit('preliminary', 900, 800, 1000, true) },
      ],
      [600, 1100],
    );

    const [solid] = rules(svg, false);
    const [dashed] = rules(svg, true);

    expect(dashed).toBeGreaterThan(solid);
  });

  test('prints the score of a system below the axis start beside its arrowhead', () => {
    const svg = draw(
      [
        { label: 'top', refit: refit('top', 1000, 950, 1050) },
        { label: 'far', refit: refit('far', 233, 81, 321) },
      ],
      [400, 1100],
    );

    const labels = [...svg.querySelectorAll('g[aria-label="text"] text')].map(
      (node) => node.textContent,
    );

    expect(labels).toEqual(['233']);
  });
});

describe('CategoryForests', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'ResizeObserver',
      class {
        constructor(private readonly callback: ResizeObserverCallback) {}
        observe() {
          this.callback([{ contentRect: { width: 600 } } as ResizeObserverEntry], this as never);
        }
        disconnect() {}
      },
    );
    Object.defineProperty(document, 'fonts', {
      configurable: true,
      value: { ready: Promise.resolve() },
    });
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
      font: '',
      measureText: (text: string) => ({ width: text.length * 7 }),
    } as never);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  test('draws every category on the same axis', async () => {
    const board = (category: string, standings: CategoryBoard['standings']): CategoryBoard => ({
      category,
      votes: 40,
      standings,
      unranked: [],
    });
    const categories = [
      board('strong', [refit('a', 1400, 1300, 1500), refit('b', 1000, 950, 1050)]),
      board('weak', [refit('a', 1000, 950, 1050), refit('b', 750, 700, 800)]),
    ];

    const { container } = render(
      <CategoryForests categories={categories} systems={['a', 'b']} names={{ a: 'A', b: 'B' }} />,
    );
    await vi.waitFor(() => {
      expect(container.querySelectorAll('svg')).toHaveLength(2);
    });

    const axes = [...container.querySelectorAll('svg')].map((svg) =>
      [...svg.querySelectorAll('g[aria-label="x-axis tick label"] text')].map(
        (node) => node.textContent,
      ),
    );

    expect(axes[0]).not.toEqual([]);
    expect(axes[1]).toEqual(axes[0]);
  });
});
