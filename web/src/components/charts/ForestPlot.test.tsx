// @vitest-environment jsdom
import * as Plot from '@observablehq/plot';
import { cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';

import { CategoryForests } from '@/components/charts/CategoryForests';
import {
  forestPlotHeight,
  forestPlotOptions,
  type ForestPlotRow,
} from '@/components/charts/ForestPlot';
import type { CategoryBoard, CategoryStanding } from '@/lib/report';
import { stubPlotFrame } from '@/test/plot';

const rules = {
  preliminary_decisive_votes: 40,
  preliminary_decisive_vote_unit: 'raw-votes' as const,
  rater_agreement_floor: 0.7,
};
const refit = (system: string, score: number, decisive = 40) => ({
  system,
  score,
  interval: null,
  votes: 40,
  effective_votes: 40,
  decisive_votes: decisive,
});

const frame = { width: 600, measure: (text: string) => text.length * 7 };

function draw(rows: ForestPlotRow[], domain: [number, number]): Element {
  const labels = rows.map((row) => row.label);
  return Plot.plot({
    ...forestPlotOptions({ rows, labels, domain, rules, anchorScore: 1000 }, frame),
    width: frame.width,
    height: forestPlotHeight(labels),
  });
}

describe('forestPlotOptions', () => {
  test('draws preliminary coverage as a hollow point without interval marks', () => {
    const svg = draw(
      [
        { label: 'settled', refit: refit('settled', 1000) },
        { label: 'preliminary', refit: refit('preliminary', 900, 20) },
      ],
      [600, 1100],
    );

    const points = [...svg.querySelectorAll('g[aria-label="dot"] circle')];

    expect(points.map((point) => point.getAttribute('fill'))).toEqual([
      'var(--chart-series)',
      'var(--background)',
    ]);
    expect(svg.querySelector('[aria-description="95% interval"]')).toBeNull();
  });

  test('prints the score of a system below the axis start beside its arrowhead', () => {
    const svg = draw(
      [
        { label: 'top', refit: refit('top', 1000) },
        { label: 'far', refit: refit('far', 233) },
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
    stubPlotFrame();
  });

  afterEach(cleanup);

  test('draws every category on the same axis, labelled by display name', async () => {
    const board = (category: string, standings: CategoryStanding[]): CategoryBoard => ({
      category,
      votes: 40,
      effective_votes: 40,
      standings,
      unranked: [],
    });
    const categories = [
      board('strong', [refit('a', 1400), refit('b', 1000)]),
      board('weak', [refit('a', 1000), refit('b', 750)]),
    ];

    const { container } = render(
      <CategoryForests
        categories={categories}
        systems={['a', 'b']}
        names={{ a: 'A', b: 'B' }}
        rules={rules}
        anchorScore={1000}
      />,
    );
    await vi.waitFor(() => {
      expect(container.querySelectorAll('svg')).toHaveLength(2);
    });

    const ticks = (axis: 'x' | 'y') =>
      [...container.querySelectorAll('svg')].map((svg) =>
        [...svg.querySelectorAll(`g[aria-label="${axis}-axis tick label"] text`)].map(
          (node) => node.textContent,
        ),
      );
    const axes = ticks('x');

    expect(axes[0]).not.toEqual([]);
    expect(axes[1]).toEqual(axes[0]);
    expect(ticks('y')).toEqual([
      ['A', 'B'],
      ['A', 'B'],
    ]);
  });
});
