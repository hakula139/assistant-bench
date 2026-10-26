import * as Plot from '@observablehq/plot';

/** Room between a row label and the plot, in CSS pixels. */
export const LABEL_GAP = 12;

/** The left margin that fits the widest of `labels` and its gap to the plot, in CSS pixels. */
export function labelMargin(labels: string[], measure: (text: string) => number): number {
  return Math.max(...labels.map(measure)) + LABEL_GAP;
}

/** Gridlines across one axis at `ticks`, a count or the values. */
export function grid(axis: 'x' | 'y', ticks: number | number[]): Plot.Markish {
  const options = { ticks, stroke: 'var(--chart-grid)', strokeOpacity: 1 };
  return axis === 'x' ? Plot.gridX(options) : Plot.gridY(options);
}

/** The rule at a reference value, such as the anchor score or an even win rate. */
export function referenceRule(value: number): Plot.Markish {
  return Plot.ruleX([value], { stroke: 'var(--chart-reference)' });
}

/** A tooltip in the chart's own font, which Plot would otherwise set to its system default. */
export function tip(data: Plot.Data, options: Plot.TipOptions): Plot.Markish {
  return Plot.tip(data, { ...options, fontFamily: 'inherit' });
}
