import * as Plot from '@observablehq/plot';
import { ticks } from 'd3-array';
import { useCallback } from 'react';

import { grid, labelMargin, tip } from '@/components/charts/chart-marks';
import { PlotFigure, type PlotFrame } from '@/components/charts/PlotFigure';
import { logTicks } from '@/components/charts/ticks';
import { formatScore } from '@/lib/format';

export interface ScatterPoint {
  system: string;
  /** The display name, which labels the point. */
  name: string;
  x: number;
  score: number;
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
const MARGIN = { top: 24, right: 16, bottom: 40 };
const INSET = { x: 16, y: 8 };
/** The score ticks the plot's height fits, at Plot's own default density. */
const SCORE_TICKS = Math.round((HEIGHT - MARGIN.top - MARGIN.bottom) / 35);
const LABEL_HEIGHT = 14;
const DOT_RADIUS = 5;

interface Box {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

/** Where a label sits beside its point, as Plot text options. */
const PLACEMENTS = [
  { dx: -7, dy: -11, textAnchor: 'end' },
  { dx: 7, dy: -11, textAnchor: 'start' },
  { dx: 7, dy: 13, textAnchor: 'start' },
] as const;

type Placement = (typeof PLACEMENTS)[number];

const overlaps = (a: Box, b: Box) =>
  a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;

const around = (x: number, y: number, halfWidth: number, halfHeight: number): Box => ({
  left: x - halfWidth,
  right: x + halfWidth,
  top: y - halfHeight,
  bottom: y + halfHeight,
});

const span = (x1: number, y1: number, x2: number, y2: number): Box => ({
  left: Math.min(x1, x2) - 1,
  right: Math.max(x1, x2) + 1,
  top: Math.min(y1, y2) - 1,
  bottom: Math.max(y1, y2) + 1,
});

/**
 * Places frontier labels clear of existing marks, preferring the upper-left.
 * Dots and prior labels take precedence over the step line.
 */
export function placeLabels(
  frontier: ScatterPoint[],
  points: ScatterPoint[],
  position: (x: number, score: number) => [number, number],
  measure: (text: string) => number,
  bounds: Box,
): Map<ScatterPoint, Placement> {
  const marks = points.map((point) =>
    around(...position(point.x, point.score), DOT_RADIUS, DOT_RADIUS),
  );
  const lines = frontier.slice(1).flatMap((point, i) => {
    const [x1, y1] = position(frontier[i].x, frontier[i].score);
    const [x2, y2] = position(point.x, point.score);

    return [span(x1, y1, x2, y1), span(x2, y1, x2, y2)];
  });

  const inside = (box: Box) =>
    box.left >= bounds.left &&
    box.right <= bounds.right &&
    box.top >= bounds.top &&
    box.bottom <= bounds.bottom;
  const clear = (box: Box, obstacles: Box[]) => !obstacles.some((other) => overlaps(box, other));

  const placed = new Map<ScatterPoint, Placement>();

  for (const point of frontier) {
    const [x, y] = position(point.x, point.score);
    const width = measure(point.name);
    const box = ({ dx, dy, textAnchor }: Placement): Box => {
      const left = textAnchor === 'end' ? x + dx - width : x + dx;

      return {
        left,
        right: left + width,
        top: y + dy - LABEL_HEIGHT / 2,
        bottom: y + dy + LABEL_HEIGHT / 2,
      };
    };

    const tiers = [
      (candidate: Box) => inside(candidate) && clear(candidate, marks) && clear(candidate, lines),
      (candidate: Box) => inside(candidate) && clear(candidate, marks),
      inside,
    ];

    const placement =
      tiers.map((fits) => PLACEMENTS.find((candidate) => fits(box(candidate)))).find(Boolean) ??
      PLACEMENTS[0];

    placed.set(point, placement);
    marks.push(box(placement));
  }

  return placed;
}

const extent = (values: number[]): [number, number] => [Math.min(...values), Math.max(...values)];

/**
 * Score against cost or latency, using a linear axis when the metric includes
 * zero. The Pareto frontier is drawn and its systems labelled, while the others name themselves
 * on hover and in the table twin.
 */
export function ParetoScatter({ points, label, format }: ParetoScatterProps) {
  const plot = useCallback(
    ({ width, measure }: PlotFrame): Plot.PlotOptions => {
      const frontier = paretoFrontier(points);
      const xDomain = extent(points.map((point) => point.x));
      const xOptions: Plot.ScaleOptions = {
        type: xDomain[0] === 0 ? 'linear' : 'log',
        domain: xDomain,
      };

      const y = Plot.scale({
        y: {
          domain: [
            Math.min(...points.map((point) => point.score)),
            Math.max(...points.map((point) => point.score)),
          ],
          nice: true,
          range: [HEIGHT - MARGIN.bottom - INSET.y, MARGIN.top + INSET.y],
        },
      });
      const [low, high] = y.domain as [number, number];
      const scoreTicks = ticks(low, high, SCORE_TICKS);
      const marginLeft = labelMargin(scoreTicks.map(formatScore), measure);

      const x = Plot.scale({
        x: {
          ...xOptions,
          range: [marginLeft + INSET.x, width - MARGIN.right - INSET.x],
        },
      });

      const placements = placeLabels(
        frontier,
        points,
        (value, score) => [x.apply(value) as number, y.apply(score) as number],
        measure,
        { left: marginLeft, right: width, top: 0, bottom: HEIGHT - MARGIN.bottom },
      );
      const tickWidth = Math.max(...xDomain.map((value) => measure(format(value))));

      const metricTicks = logTicks(
        xDomain,
        Math.max(2, Math.floor((width - marginLeft - MARGIN.right) / (tickWidth + 24))),
      );

      return {
        marginTop: MARGIN.top,
        marginRight: MARGIN.right,
        marginBottom: MARGIN.bottom,
        marginLeft,
        x: { ...xOptions, inset: INSET.x },
        y: {
          domain: y.domain,
          ticks: scoreTicks,
          tickFormat: formatScore,
          label: 'Score',
          inset: INSET.y,
        },
        marks: [
          grid('x', metricTicks),
          grid('y', scoreTicks),
          Plot.axisX(metricTicks, {
            text: format,
            label,
            labelAnchor: 'right',
            labelArrow: 'none',
          }),
          Plot.line(frontier, {
            x: 'x',
            y: 'score',
            curve: 'step-after',
            stroke: 'var(--faint)',
            strokeWidth: 1.5,
          }),
          Plot.dot(points, {
            x: 'x',
            y: 'score',
            r: DOT_RADIUS,
            fill: 'var(--chart-series)',
            stroke: 'var(--background)',
            strokeWidth: 2,
          }),
          ...PLACEMENTS.map((placement) =>
            Plot.text(
              frontier.filter((point) => placements.get(point) === placement),
              { x: 'x', y: 'score', text: 'name', fill: 'var(--foreground)', ...placement },
            ),
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
          tip(
            points,
            Plot.pointer({
              x: 'x',
              y: 'score',
              title: (point: ScatterPoint) =>
                [
                  point.name,
                  `Score ${formatScore(point.score)}`,
                  `${label} ${format(point.x)}`,
                ].join('\n'),
            }),
          ),
        ],
      };
    },
    [points, label, format],
  );

  return <PlotFigure plot={plot} height={HEIGHT} />;
}
