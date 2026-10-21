import * as Plot from '@observablehq/plot';
import { useCallback } from 'react';

import { PlotFigure, type PlotFrame } from '@/components/charts/PlotFigure';
import { logTicks } from '@/components/charts/ticks';
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
const MARGIN = { top: 24, right: 16, bottom: 40, left: 48 };
const INSET = { x: 16, y: 8 };
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
 * Places each frontier label at the first spot inside the plot that stays clear of the dots, the
 * earlier labels, the interval whiskers, and the step line, giving up the whiskers and the line
 * before the dots and labels when nothing is clear. Above and to the left of a frontier point
 * comes first, since every dominated point lies below or to the right of it.
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
  const lines = [
    ...points.map((point) =>
      span(...position(point.x, point.lower), ...position(point.x, point.upper)),
    ),
    ...frontier.slice(1).flatMap((point, i) => {
      const [x1, y1] = position(frontier[i].x, frontier[i].score);
      const [x2, y2] = position(point.x, point.score);
      return [span(x1, y1, x2, y1), span(x2, y1, x2, y2)];
    }),
  ];
  const inside = (box: Box) =>
    box.left >= bounds.left &&
    box.right <= bounds.right &&
    box.top >= bounds.top &&
    box.bottom <= bounds.bottom;
  const clear = (box: Box, obstacles: Box[]) => !obstacles.some((other) => overlaps(box, other));

  const placed = new Map<ScatterPoint, Placement>();
  for (const point of frontier) {
    const [x, y] = position(point.x, point.score);
    const width = measure(point.system);
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
 * Score with its interval against a cost or latency on a log axis. The Pareto frontier is drawn
 * and its systems labelled, while the others name themselves on hover and in the table twin.
 */
export function ParetoScatter({ points, label, format }: ParetoScatterProps) {
  const plot = useCallback(
    ({ width, measure }: PlotFrame): Plot.PlotOptions => {
      const frontier = paretoFrontier(points);
      const xDomain = extent(points.map((point) => point.x));
      const x = Plot.scale({
        x: {
          type: 'log',
          domain: xDomain,
          range: [MARGIN.left + INSET.x, width - MARGIN.right - INSET.x],
        },
      });
      const y = Plot.scale({
        y: {
          domain: [
            Math.min(...points.map((point) => point.lower)),
            Math.max(...points.map((point) => point.upper)),
          ],
          nice: true,
          range: [HEIGHT - MARGIN.bottom - INSET.y, MARGIN.top + INSET.y],
        },
      });
      const placements = placeLabels(
        frontier,
        points,
        (value, score) => [x.apply(value) as number, y.apply(score) as number],
        measure,
        { left: MARGIN.left, right: width, top: 0, bottom: HEIGHT - MARGIN.bottom },
      );
      const tickWidth = Math.max(...xDomain.map((value) => measure(format(value))));
      const ticks = logTicks(
        xDomain,
        Math.max(2, Math.floor((width - MARGIN.left - MARGIN.right) / (tickWidth + 24))),
      );
      return {
        height: HEIGHT,
        marginTop: MARGIN.top,
        marginRight: MARGIN.right,
        marginBottom: MARGIN.bottom,
        marginLeft: MARGIN.left,
        x: { type: 'log', domain: xDomain, inset: INSET.x },
        y: { domain: y.domain, label: 'Score', grid: true, inset: INSET.y },
        marks: [
          Plot.gridX(ticks),
          Plot.axisX(ticks, { text: format, label, labelAnchor: 'right', labelArrow: 'none' }),
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
            r: DOT_RADIUS,
            fill: 'var(--chart-series)',
            stroke: 'var(--background)',
            strokeWidth: 2,
          }),
          ...PLACEMENTS.map((placement) =>
            Plot.text(
              frontier.filter((point) => placements.get(point) === placement),
              { x: 'x', y: 'score', text: 'system', fill: 'var(--foreground)', ...placement },
            ),
          ),
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
    },
    [points, label, format],
  );

  return <PlotFigure plot={plot} height={HEIGHT} />;
}
