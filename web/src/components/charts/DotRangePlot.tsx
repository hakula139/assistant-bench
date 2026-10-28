import * as Plot from '@observablehq/plot';
import { nice } from 'd3-array';
import { useCallback } from 'react';

import {
  grid,
  LABEL_GAP,
  labelMargin,
  referenceRule,
  tickCount,
  tip,
} from '@/components/charts/chart-marks';
import { PlotFigure, type PlotFrame } from '@/components/charts/PlotFigure';
import { linearTicks } from '@/components/charts/ticks';
import { formatCount, formatPercent, formatSeconds } from '@/lib/format';

export interface DotRange {
  label: string;
  value: number;
  /** The range drawn behind the dot, such as an interval or the p10 to p90 spread. */
  range?: { lower: number; upper: number };
  /** Draws the row in the emphasis color, as for the page's own system. */
  emphasis?: boolean;
}

/** Named so that Astro pages, which pass only serializable props to islands, can choose one. */
type ValueFormat = 'percent' | 'tokens' | 'seconds';

interface DotRangePlotProps {
  rows: DotRange[];
  /** The x axis label, naming the measure and its unit. */
  label: string;
  format: ValueFormat;
  /** A reference value drawn as a rule, such as an even win rate. */
  baseline?: number;
  /** The x axis extent for a bounded measure, such as a rate from 0 to 1. */
  domain?: [number, number];
}

const ROW_HEIGHT = 32;
const MARGIN_RIGHT = 24;

const formats: Record<ValueFormat, (value: number) => string> = {
  percent: formatPercent,
  tokens: formatCount,
  seconds: formatSeconds,
};

/** The span of every value, range end and baseline the plot draws. */
function extent(rows: DotRange[], baseline?: number): [number, number] {
  const values = rows.flatMap((row) => [
    row.value,
    ...(row.range ? [row.range.lower, row.range.upper] : []),
  ]);
  if (baseline !== undefined) {
    values.push(baseline);
  }

  return [Math.min(...values), Math.max(...values)];
}

export function DotRangePlot({ rows, label, format, baseline, domain }: DotRangePlotProps) {
  const height = rows.length * ROW_HEIGHT + 48;
  const tick = formats[format];

  const plot = useCallback(
    ({ width, measure }: PlotFrame): Plot.PlotOptions => {
      const color = (row: DotRange) =>
        row.emphasis ? 'var(--chart-emphasis)' : 'var(--chart-series)';
      const ranged = rows.filter((row) => row.range !== undefined);
      const [low, high] = nice(
        ...(domain ?? extent(rows, baseline)),
        // Plot's default count when it rounds a domain.
        10,
      );
      const marginLeft = labelMargin(
        rows.map((row) => row.label),
        measure,
      );
      const ticks = linearTicks(
        low,
        high,
        tickCount(width - marginLeft - MARGIN_RIGHT, [low, high].map(tick), measure),
      );

      return {
        marginLeft,
        marginRight: MARGIN_RIGHT,
        marginBottom: 40,
        x: { label, domain: [low, high], tickFormat: tick, ticks },
        y: {
          domain: rows.map((row) => row.label),
          label: null,
          tickSize: 0,
          tickPadding: LABEL_GAP,
        },
        color: { type: 'identity' },
        marks: [
          grid('x', ticks),
          baseline === undefined ? null : referenceRule(baseline),
          Plot.ruleY(ranged, {
            y: 'label',
            x1: (row: DotRange) => row.range?.lower,
            x2: (row: DotRange) => row.range?.upper,
            stroke: color,
            strokeWidth: 2,
            strokeOpacity: 0.55,
          }),
          Plot.dot(rows, {
            x: 'value',
            y: 'label',
            r: 5,
            fill: color,
            stroke: 'var(--background)',
            strokeWidth: 2,
          }),
          tip(
            rows,
            Plot.pointerY({
              x: 'value',
              y: 'label',
              title: (row: DotRange) =>
                row.range
                  ? `${row.label}\n${tick(row.value)} (${tick(row.range.lower)}–${tick(row.range.upper)})`
                  : `${row.label}\n${tick(row.value)}`,
            }),
          ),
        ],
      };
    },
    [rows, label, tick, baseline, domain],
  );

  return <PlotFigure plot={plot} height={height} label={label} />;
}
