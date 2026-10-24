import * as Plot from '@observablehq/plot';

import type { Clipped } from '@/lib/forest';
import { formatScore } from '@/lib/format';

/** One score point, clipped to the plot's domain. */
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

/** The rule a forest plot draws at the anchor score, as the static `Forest` draws it. */
export function anchorRule(score: number): Plot.Markish {
  return Plot.ruleX([score], { stroke: 'var(--faint)', strokeOpacity: 0.4 });
}

/** Hollow points mark preliminary coverage. A clipped point keeps an arrowhead. */
export function forestMarks(data: ForestDatum[], [start]: [number, number]): Plot.Markish[] {
  return [
    Plot.dot(
      data.filter((datum) => datum.clipped.offScale),
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

/** Keeps a clipped point's actual value visible on plots without a score column. */
export function offScaleLabels(data: ForestDatum[]): Plot.Markish {
  return Plot.text(
    data.filter((datum) => datum.clipped.offScale),
    {
      x: (datum: ForestDatum) => datum.clipped.score,
      y: 'y',
      text: (datum: ForestDatum) => formatScore(datum.score),
      dx: 9,
      textAnchor: 'start',
      fill: 'var(--muted-foreground)',
    },
  );
}
