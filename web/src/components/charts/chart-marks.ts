import * as Plot from '@observablehq/plot';

/** Room between a row label and the plot, in CSS pixels. */
export const LABEL_GAP = 12;

/** The left margin that fits the widest of `labels` and its gap to the plot, in CSS pixels. */
export function labelMargin(labels: string[], measure: (text: string) => number): number {
  return Math.max(...labels.map(measure)) + LABEL_GAP;
}

/**
 * How many x ticks fit along `span` CSS pixels when each label is as wide as the widest of `labels`
 * and keeps 24 pixels from the next, and never fewer than two.
 */
export function tickCount(
  span: number,
  labels: string[],
  measure: (text: string) => number,
): number {
  const widest = Math.max(...labels.map(measure));
  return Math.max(2, Math.floor(span / (widest + 24)));
}

/** Text options that ring a label in the page color, so that lines drawn before it do not cross it. */
export const HALO = { stroke: 'var(--background)', strokeWidth: 4 };

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
