import * as Plot from '@observablehq/plot';

import type { Clipped } from '@/lib/forest';

/** One interval on a forest plot, already clipped to the plot's domain. */
export interface ForestDatum {
  /** The row's position on the y scale. */
  y: string | number;
  clipped: Clipped;
  preliminary: boolean;
}

const SERIES = 'var(--chart-series)';
const SURFACE = 'var(--background)';
const DASH = '5 4';

/**
 * The marks of a forest plot: each interval as a 3 px rule, dashed while preliminary, then an
 * arrowhead where the axis cuts it, then a dot at the score, hollow while preliminary. A score
 * below the axis start keeps only the arrowhead. Plots that take these marks need
 * `color: { type: 'identity' }`.
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
