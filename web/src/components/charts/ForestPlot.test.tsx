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
import type { Category } from '@/lib/categories';
import type { CategoryBoard, CategoryStanding } from '@/lib/report';
import { stubPlotFrame } from '@/test/plot';

const rules = { preliminary_decisive_votes: 40 };
const refit = (system: string, score: number, decisive = 40) => ({
  system,
  score,
  interval: null,
  votes: 40,
  effective_votes: 40,
  decisive_votes: decisive,
});

const frame = { width: 600, measure: (text: string) => text.length * 7 };

function draw(
  rows: ForestPlotRow[],
  domain: [number, number],
  labels = rows.map((row) => row.label),
  unranked: string[] = [],
): Element {
  return Plot.plot({
    ...forestPlotOptions({ rows, labels, unranked, domain, rules, anchorScore: 1000 }, frame),
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

  test('draws a score below the axis start as an arrowhead on the start gridline, with its score past the arrowhead', () => {
    const svg = draw(
      [
        { label: 'top', refit: refit('top', 1000) },
        { label: 'far', refit: refit('far', 233) },
      ],
      [400, 1100],
    );

    const arrow = svg.querySelector('g[aria-label="dot"] path');
    const start = Number(svg.querySelector('g[aria-label="x-grid"] line')?.getAttribute('x1'));
    const offset = (element: Element | null | undefined) =>
      Number(/translate\((-?[\d.]+)/.exec(element?.getAttribute('transform') ?? '')?.[1]);
    const labels = [...svg.querySelectorAll('g[aria-label="text"] text')];
    const label = labels[0];

    expect(arrow?.getAttribute('d')).toBe('M0,0L10,-6L10,6Z');
    expect(offset(arrow)).toBe(start);
    expect(labels.map((node) => node.textContent)).toEqual(['233']);
    expect(offset(label.parentElement) + offset(label)).toBeGreaterThan(
      offset(arrow?.parentElement) + offset(arrow) + 10,
    );
  });

  test('prints a negative score below the axis with a minus sign, haloed against crossing rules', () => {
    const svg = draw(
      [
        { label: 'top', refit: refit('top', 1000) },
        { label: 'far', refit: refit('far', -161) },
      ],
      [400, 1100],
    );

    const label = svg.querySelector('g[aria-label="text"]');

    expect(label?.textContent).toBe('\u2212161');
    expect(label?.getAttribute('stroke')).toBe('var(--background)');
    expect(label?.getAttribute('paint-order')).toBe('stroke');
  });

  test('names why a row without a point is empty', () => {
    const svg = draw(
      [{ label: 'top', refit: refit('top', 1000) }],
      [600, 1100],
      ['top', 'silent', 'loose'],
      ['loose'],
    );

    const y = (element: Element | null | undefined) =>
      /translate\([^,]+,(-?[\d.]+)/.exec(element?.getAttribute('transform') ?? '')?.[1];
    const rows = Object.fromEntries(
      [...svg.querySelectorAll('g[aria-label="y-axis tick label"] text')].map((tick) => [
        tick.textContent,
        y(tick),
      ]),
    );
    const notes = [...svg.querySelectorAll('g[aria-label="text"] text')].map((note) => [
      note.textContent,
      y(note),
    ]);

    expect(notes).toEqual([
      ['no votes', rows.silent],
      ['unranked', rows.loose],
    ]);
  });

  test.each<[string, [number, number], number]>([
    ['on the axis', [600, 1100], 1],
    ['below the axis start', [1200, 1800], 0],
  ])('rules an anchor %s only where the axis holds it', (_, domain, ruled) => {
    const svg = draw([{ label: 'top', refit: refit('top', domain[1]) }], domain);

    const references = [...svg.querySelectorAll('g[aria-label="rule"]')].filter(
      (rule) => rule.getAttribute('stroke') === 'var(--chart-reference)',
    );

    expect(references).toHaveLength(ruled);
  });
});

describe('CategoryForests', () => {
  beforeEach(() => {
    stubPlotFrame();
  });

  afterEach(cleanup);

  test('draws every category on the same axis, labelled by display name', async () => {
    const board = (category: Category, standings: CategoryStanding[]): CategoryBoard => ({
      category,
      votes: 40,
      effective_votes: 40,
      standings,
      unranked: [],
    });
    const categories = [
      board('docs', [refit('a', 1400), refit('b', 1000)]),
      board('message', [refit('a', 1000), refit('b', 750)]),
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

    const captions = [...container.querySelectorAll('figcaption')].map((node) => node.textContent);

    expect(captions).toEqual(['Docs, 40 votes', 'Message, 40 votes']);

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
