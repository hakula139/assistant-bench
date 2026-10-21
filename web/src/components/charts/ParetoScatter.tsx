import * as Plot from '@observablehq/plot';
import { useCallback } from 'react';

import { PlotFigure } from '@/components/charts/PlotFigure';
import { formatInterval, formatScore } from '@/lib/format';

export interface ScatterPoint {
  system: string;
  x: number;
  score: number;
  lower: number;
  upper: number;
}

/**
 * Returns the points no other point beats on both axes, from the lowest `x` up: each has a higher
 * score than every point to its left, or ties the previous one on both axes.
 */
export function paretoFrontier(points: ScatterPoint[]): ScatterPoint[] {
  const frontier: ScatterPoint[] = [];
  for (const point of points.toSorted((a, b) => a.x - b.x || b.score - a.score)) {
    const best = frontier.at(-1);
    if (
      best === undefined ||
      point.score > best.score ||
      (point.x === best.x && point.score === best.score)
    ) {
      frontier.push(point);
    }
  }
  return frontier;
}

interface ParetoScatterProps {
  points: ScatterPoint[];
  /** The x axis label, naming the measure and its unit. */
  label: string;
  format: (value: number) => string;
}

const HEIGHT = 320;

/**
 * Score with its interval against a cost or latency on a log axis. The Pareto frontier is drawn
 * and its systems labelled, while the others name themselves on hover and in the table twin.
 */
export function ParetoScatter({ points, label, format }: ParetoScatterProps) {
  const plot = useCallback((): Plot.PlotOptions => {
    const frontier = paretoFrontier(points);
    return {
      height: HEIGHT,
      marginLeft: 48,
      marginRight: 96,
      marginBottom: 40,
      x: { type: 'log', label, tickFormat: format, ticks: 4, grid: true, inset: 16 },
      y: { label: 'Score', grid: true, nice: true, inset: 8 },
      marks: [
        Plot.line(frontier, {
          x: 'x',
          y: 'score',
          curve: 'step-after',
          stroke: 'var(--faint)',
          strokeWidth: 1.5,
        }),
        Plot.ruleX(points, {
          x: 'x',
          y1: 'lower',
          y2: 'upper',
          stroke: 'var(--chart-series)',
          strokeOpacity: 0.45,
          strokeWidth: 2,
        }),
        Plot.dot(points, {
          x: 'x',
          y: 'score',
          r: 5,
          fill: 'var(--chart-series)',
          stroke: 'var(--background)',
          strokeWidth: 2,
        }),
        Plot.text(frontier, {
          x: 'x',
          y: 'score',
          text: 'system',
          dx: 7,
          dy: -11,
          textAnchor: 'start',
          fill: 'var(--foreground)',
        }),
        Plot.ruleX(
          points,
          Plot.pointer({
            x: 'x',
            y1: 'lower',
            y2: 'upper',
            py: 'score',
            stroke: 'var(--chart-emphasis)',
            strokeWidth: 2,
          }),
        ),
        Plot.dot(
          points,
          Plot.pointer({
            x: 'x',
            y: 'score',
            r: 6,
            fill: 'var(--chart-emphasis)',
            stroke: 'var(--background)',
            strokeWidth: 2,
          }),
        ),
        Plot.tip(
          points,
          Plot.pointer({
            x: 'x',
            y: 'score',
            title: (point: ScatterPoint) =>
              [
                point.system,
                `Score ${formatScore(point.score)} (${formatInterval(point)})`,
                `${label} ${format(point.x)}`,
              ].join('\n'),
            fontFamily: 'inherit',
          }),
        ),
      ],
    };
  }, [points, label, format]);

  return <PlotFigure plot={plot} height={HEIGHT} />;
}
