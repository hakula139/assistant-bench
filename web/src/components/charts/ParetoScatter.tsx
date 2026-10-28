import * as Plot from '@observablehq/plot';
import { ticks } from 'd3-array';
import { useCallback } from 'react';

import {
  grid,
  HALO,
  LABEL_GAP,
  labelMargin,
  tickCount,
  tip,
} from '@/components/charts/chart-marks';
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
const TICK_SIZE = 6;
const LABEL_HEIGHT = 14;
const DOT_RADIUS = 5;
/** The width of the ring around each dot, whose outer half widens the dot. */
const DOT_STROKE = 2;
/** How much farther than the first ring the second ring sits, in pixels on each axis. */
const LEADER_GAP = 14;

interface Box {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

/** Where a label sits beside its point, as Plot text options. */
interface Placement {
  dx: number;
  dy: number;
  textAnchor: 'start' | 'middle' | 'end';
}

/** Spots beside the dot, in order of preference, where a label reads as the dot's own. */
const NEAR: Placement[] = [
  { dx: -7, dy: -11, textAnchor: 'end' },
  { dx: 7, dy: -11, textAnchor: 'start' },
  { dx: 7, dy: 13, textAnchor: 'start' },
  { dx: -7, dy: 13, textAnchor: 'end' },
  { dx: 0, dy: -13, textAnchor: 'middle' },
  { dx: 0, dy: 15, textAnchor: 'middle' },
];

/** The same spots `LEADER_GAP` farther out, where a leader line long enough to see ties a label. */
const FAR: Placement[] = NEAR.map(({ dx, dy, textAnchor }) => ({
  dx: dx + Math.sign(dx) * LEADER_GAP,
  dy: dy + Math.sign(dy) * LEADER_GAP,
  textAnchor,
}));

const PLACEMENTS = [...NEAR, ...FAR];

/**
 * A frontier label's placement, with the point on the label's edge nearest its dot, in pixels, when
 * the label sits in the far ring and needs a leader line to its dot.
 */
export interface Label {
  placement: Placement;
  leader?: [number, number];
}

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

/** The point of `box` nearest `[x, y]`, which is `[x, y]` itself inside the box. */
const nearest = (box: Box, [x, y]: [number, number]): [number, number] => [
  Math.min(Math.max(x, box.left), box.right),
  Math.min(Math.max(y, box.top), box.bottom),
];

const distance = (box: Box, center: [number, number]) => {
  const [x, y] = nearest(box, center);
  return Math.hypot(x - center[0], y - center[1]);
};

/** The distance from `point` to the segment from `from` to `to`. */
const segmentDistance = (
  [px, py]: [number, number],
  [x1, y1]: [number, number],
  [x2, y2]: [number, number],
) => {
  const length = (x2 - x1) ** 2 + (y2 - y1) ** 2;
  const t =
    length === 0
      ? 0
      : Math.min(1, Math.max(0, ((px - x1) * (x2 - x1) + (py - y1) * (y2 - y1)) / length));
  return Math.hypot(px - (x1 + t * (x2 - x1)), py - (y1 + t * (y2 - y1)));
};

/**
 * Places frontier labels clear of dots and earlier labels, preferring the upper-left. A label beside
 * its dot must sit closer to it than to any other dot, so that no label reads as another system's.
 * Where no spot beside the dot qualifies, the label moves to a farther ring and takes a leader line
 * that passes no other dot. A label that fits nowhere is left off, since the tip and the table twin
 * still name the system. Prefers spots the step line does not cross, which the labels' halo masks.
 */
export function placeLabels(
  frontier: ScatterPoint[],
  points: ScatterPoint[],
  position: (x: number, score: number) => [number, number],
  measure: (text: string) => number,
  bounds: Box,
): Map<ScatterPoint, Label> {
  const centers = new Map(points.map((point) => [point, position(point.x, point.score)]));
  const reach = DOT_RADIUS + DOT_STROKE / 2;
  const marks = [...centers.values()].map((center) => around(...center, reach, reach));
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

  const placed = new Map<ScatterPoint, Label>();

  for (const point of frontier) {
    const center = position(point.x, point.score);
    const [x, y] = center;
    const width = measure(point.name);
    const box = ({ dx, dy, textAnchor }: Placement): Box => {
      const offset = { start: 0, middle: width / 2, end: width }[textAnchor];
      const left = x + dx - offset;

      return {
        left,
        right: left + width,
        top: y + dy - LABEL_HEIGHT / 2,
        bottom: y + dy + LABEL_HEIGHT / 2,
      };
    };
    const others = [...centers].filter(([other]) => other !== point).map(([, at]) => at);
    const own = (candidate: Box) => {
      const gap = distance(candidate, center);
      return others.every((other) => distance(candidate, other) > gap);
    };
    const leaderClear = (candidate: Box) => {
      const end = nearest(candidate, center);
      return others.every((other) => segmentDistance(other, center, end) > reach);
    };

    const clearOfAll = (candidate: Box) =>
      inside(candidate) && clear(candidate, marks) && clear(candidate, lines);
    const clearOfMarks = (candidate: Box) => inside(candidate) && clear(candidate, marks);
    const both =
      (...checks: ((candidate: Box) => boolean)[]) =>
      (candidate: Box) =>
        checks.every((check) => check(candidate));

    const choices: [Placement[], (candidate: Box) => boolean][] = [
      [NEAR, both(clearOfAll, own)],
      [NEAR, both(clearOfMarks, own)],
      [FAR, both(clearOfAll, leaderClear)],
      [FAR, both(clearOfMarks, leaderClear)],
    ];
    const placement = choices
      .map(([ring, fits]) => ring.find((candidate) => fits(box(candidate))))
      .find((found) => found !== undefined);
    if (placement === undefined) {
      continue;
    }

    const label = box(placement);
    if (FAR.includes(placement)) {
      const end = nearest(label, center);
      placed.set(point, { placement, leader: end });
      lines.push(span(x, y, ...end));
    } else {
      placed.set(point, { placement });
    }
    marks.push(label);
  }

  return placed;
}

const extent = (values: number[]): [number, number] => [Math.min(...values), Math.max(...values)];

/** Maps a pixel position back to data through a linear or log scale, which always has an inverse. */
const invert = (scale: Plot.Scale, pixel: number): number => {
  if (scale.invert === undefined) {
    throw new Error(`A ${String(scale.type)} scale has no inverse`);
  }
  return scale.invert(pixel) as number;
};

/**
 * Score against cost or latency, using a linear axis when the metric includes
 * zero. The Pareto frontier is drawn and its systems labelled where a label fits, while the others
 * name themselves on hover and in the table twin.
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

      const labels = placeLabels(
        frontier,
        points,
        (value, score) => [x.apply(value) as number, y.apply(score) as number],
        measure,
        { left: marginLeft, right: width, top: 0, bottom: HEIGHT - MARGIN.bottom },
      );
      const leaders = frontier.flatMap((point) => {
        const end = labels.get(point)?.leader;
        return end === undefined ? [] : [{ ...point, end }];
      });
      const metricTicks = logTicks(
        xDomain,
        tickCount(width - marginLeft - MARGIN.right, xDomain.map(format), measure),
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
          tickSize: TICK_SIZE,
          tickPadding: LABEL_GAP - TICK_SIZE,
          label: 'Score',
          labelOffset: marginLeft,
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
          Plot.link(leaders, {
            x1: 'x',
            y1: 'score',
            x2: ({ end }: (typeof leaders)[number]) => invert(x, end[0]),
            y2: ({ end }: (typeof leaders)[number]) => invert(y, end[1]),
            stroke: 'var(--muted-foreground)',
          }),
          Plot.dot(points, {
            x: 'x',
            y: 'score',
            r: DOT_RADIUS,
            fill: 'var(--chart-series)',
            stroke: 'var(--background)',
            strokeWidth: DOT_STROKE,
          }),
          ...PLACEMENTS.map((placement) =>
            Plot.text(
              frontier.filter((point) => labels.get(point)?.placement === placement),
              {
                x: 'x',
                y: 'score',
                text: 'name',
                fill: 'var(--foreground)',
                ...HALO,
                ...placement,
              },
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
