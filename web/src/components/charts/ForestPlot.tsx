import * as Plot from '@observablehq/plot';
import { useCallback } from 'react';

import {
  anchorRule,
  forestMarks,
  offScaleLabels,
  type ForestDatum,
} from '@/components/charts/forest-marks';
import { LABEL_GAP, PlotFigure, type PlotFrame } from '@/components/charts/PlotFigure';
import { clip, forestTicks, labelledTicks } from '@/lib/forest';
import { formatCountOf, formatInterval, formatScore } from '@/lib/format';
import { ANCHOR_SCORE } from '@/lib/ranking';
import type { CategoryBoard } from '@/lib/report';

/** A category refit, which carries its votes and preliminary status. */
export type Refit = CategoryBoard['standings'][number];

export interface ForestPlotRow {
  label: string;
  refit: Refit;
}

interface ForestPlotProps {
  rows: ForestPlotRow[];
  /** Every row label from top to bottom, including labels without a row. */
  labels: string[];
  /** The x domain, which callers share across plots so their intervals compare. */
  domain: [number, number];
  /** Names the chart, unless an enclosing figure's caption already does. */
  label?: string;
}

const ROW_HEIGHT = 30;
/** Room between the plot's left edge and the axis start, where an arrowhead points. */
const ARROW_INSET = 8;

/** Height in CSS pixels. */
export const forestPlotHeight = (labels: string[]) => labels.length * ROW_HEIGHT + 36;

export function forestPlotOptions(
  { rows, labels, domain }: ForestPlotProps,
  { width, measure }: PlotFrame,
): Plot.PlotOptions {
  const data = rows.map(({ label, refit }): ForestDatum & { refit: Refit } => ({
    y: label,
    score: refit.score,
    clipped: clip(refit, domain),
    preliminary: refit.preliminary,
    refit,
  }));
  const marginLeft = Math.max(...labels.map(measure)) + LABEL_GAP;
  const ticks = forestTicks(domain);
  const labelled = new Set(labelledTicks(domain, width - marginLeft - ARROW_INSET));
  return {
    marginLeft,
    marginRight: 16,
    marginBottom: 28,
    x: {
      domain,
      ticks,
      tickFormat: (tick: number) => (labelled.has(tick) ? formatScore(tick) : ''),
      insetLeft: ARROW_INSET,
      label: null,
      grid: true,
    },
    y: { domain: labels, label: null, tickSize: 0, tickPadding: LABEL_GAP },
    color: { type: 'identity' },
    marks: [
      anchorRule(ANCHOR_SCORE),
      forestMarks(data, domain),
      offScaleLabels(data),
      Plot.tip(
        data,
        Plot.pointerY({
          x: (datum: (typeof data)[number]) => datum.clipped.score,
          y: 'y',
          title: ({ y, refit }: (typeof data)[number]) =>
            `${String(y)}\n${formatScore(refit.score)} (${formatInterval(refit.interval)})\n${formatCountOf(refit.votes, 'vote')}${refit.preliminary ? ', preliminary' : ''}`,
          fontFamily: 'inherit',
        }),
      ),
    ],
  };
}

/** One forest plot of category refits, labelled by system or by category. */
export function ForestPlot({ rows, labels, domain, label }: ForestPlotProps) {
  const plot = useCallback(
    (frame: PlotFrame) => forestPlotOptions({ rows, labels, domain }, frame),
    [rows, labels, domain],
  );
  return <PlotFigure plot={plot} height={forestPlotHeight(labels)} label={label} />;
}
