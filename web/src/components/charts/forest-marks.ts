import * as Plot from '@observablehq/plot';

import type { Clipped } from '@/lib/forest';
import { formatScore } from '@/lib/format';

/** One interval on a forest plot, already clipped to the plot's domain. */
export interface ForestDatum {
  /** The row's position on the y scale. */
  y: string | number;
  /** The score before clipping, which a plot without a score column prints off the scale. */
  score: number;
  clipped: Clipped;
  preliminary: boolean;
}

const SERIES = 'var(--chart-series)';
const SURFACE = 'var(--background)';
const DASH = '5 4';

/** The rule a forest plot draws at the anchor score, as the static `Forest` draws it. */
export function anchorRule(score: number): Plot.Markish {
  return Plot.ruleX([score], { stroke: 'var(--faint)', strokeOpacity: 0.4 });
}

/**
 * The marks of a forest plot: each interval as a 3 px rule, dashed while preliminary, then an
 * arrowhead where the axis cuts it, then a dot at the score, hollow while preliminary. A score
 * below the axis start keeps only the arrowhead. Plots taking these marks need an identity color
 * scale, `color: { type: 'identity' }`.
 */
export function forestMarks(data: ForestDatum[], [start]: [number, number]): Plot.Markish[] {
  const rule = (preliminary: boolean) =>
    Plot.ruleY(
      data.filter((datum) => datum.preliminary === preliminary),
      {
        y: 'y',
        x1: (datum: ForestDatum) => datum.clipped.lower,
        x2: (datum: ForestDatum) => datum.clipped.upper,
        stroke: SERIES,
        strokeWidth: 3,
        ariaDescription: '95% interval',
        strokeDasharray: preliminary ? DASH : undefined,
      },
    );
  return [
    rule(false),
    rule(true),
    Plot.dot(
      data.filter((datum) => datum.clipped.cutLow),
      { x: start, y: 'y', symbol: 'triangle', rotate: -90, r: 5, fill: SERIES },
    ),
    Plot.dot(
      data.filter((datum) => !datum.clipped.offScale),
      {
        x: (datum: ForestDatum) => datum.clipped.score,
        y: 'y',
        r: 6,
        fill: (datum: ForestDatum) => (datum.preliminary ? SURFACE : SERIES),
        stroke: (datum: ForestDatum) => (datum.preliminary ? SERIES : SURFACE),
        strokeWidth: 2,
      },
    ),
  ];
}

/**
 * Prints the score of each row whose score lies below the axis start, after whatever part of its
 * interval the axis keeps, for forest plots without a score column.
 */
export function offScaleLabels(data: ForestDatum[]): Plot.Markish {
  return Plot.text(
    data.filter((datum) => datum.clipped.offScale),
    {
      x: (datum: ForestDatum) => datum.clipped.upper,
      y: 'y',
      text: (datum: ForestDatum) => formatScore(datum.score),
      dx: 9,
      textAnchor: 'start',
      fill: 'var(--muted-foreground)',
    },
  );
}
