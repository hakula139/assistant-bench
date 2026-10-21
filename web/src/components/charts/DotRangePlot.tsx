import * as Plot from '@observablehq/plot';
import { useCallback } from 'react';

import { PlotFigure } from '@/components/charts/PlotFigure';
import { formatCount, formatPercent, formatScore, formatSeconds } from '@/lib/format';

export interface DotRange {
  label: string;
  value: number;
  /** The range drawn behind the dot, such as an interval or the p10 to p90 spread. */
  range?: { lower: number; upper: number };
  /** Draws the row in the emphasis color, as for the page's own system. */
  emphasis?: boolean;
}

/** Named so that Astro pages, which pass only serializable props to islands, can choose one. */
export type ValueFormat = 'score' | 'percent' | 'tokens' | 'seconds';

interface DotRangePlotProps {
  rows: DotRange[];
  /** The x axis label, naming the measure and its unit. */
  label: string;
  format: ValueFormat;
  /** A reference value drawn as a rule, such as an even win rate. */
  baseline?: number;
}

const ROW_HEIGHT = 32;

const formats: Record<ValueFormat, (value: number) => string> = {
  score: formatScore,
  percent: formatPercent,
  tokens: formatCount,
  seconds: formatSeconds,
};

/** One dot per row on a shared axis, each with an optional range behind it. */
export function DotRangePlot({ rows, label, format, baseline }: DotRangePlotProps) {
  const height = rows.length * ROW_HEIGHT + 48;
  const tick = formats[format];

  const plot = useCallback(
    (width: number): Plot.PlotOptions => {
      const color = (row: DotRange) =>
        row.emphasis ? 'var(--chart-emphasis)' : 'var(--chart-series)';
      const ranged = rows.filter((row) => row.range !== undefined);
      return {
        height,
        marginLeft: 120,
        marginRight: 24,
        marginBottom: 40,
        x: { label, tickFormat: tick, grid: true, nice: true, ticks: Math.ceil(width / 160) },
        y: { domain: rows.map((row) => row.label), label: null, tickSize: 0 },
        color: { type: 'identity' },
        marks: [
          baseline === undefined
            ? null
            : Plot.ruleX([baseline], { stroke: 'var(--faint)', strokeOpacity: 0.8 }),
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
          Plot.tip(
            rows,
            Plot.pointerY({
              x: 'value',
              y: 'label',
              title: (row: DotRange) =>
                row.range
                  ? `${row.label}\n${tick(row.value)} (${tick(row.range.lower)}–${tick(row.range.upper)})`
                  : `${row.label}\n${tick(row.value)}`,
              fontFamily: 'inherit',
            }),
          ),
        ],
      };
    },
    [rows, label, tick, baseline, height],
  );

  return <PlotFigure plot={plot} height={height} />;
}
