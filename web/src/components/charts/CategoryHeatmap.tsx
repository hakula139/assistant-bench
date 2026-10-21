import * as Plot from '@observablehq/plot';
import { useCallback, useMemo } from 'react';

import { PlotFigure, type PlotFrame } from '@/components/charts/PlotFigure';
import { formatInterval, formatScore } from '@/lib/format';
import type { CategoryBoard } from '@/lib/report';

const STEPS = 5;
const HATCH = 'bench-hatch';
const ROW_HEIGHT = 40;
/** Room between a row label and the cells, in CSS pixels. */
const LABEL_GAP = 12;

interface Cell {
  system: string;
  category: string;
  score: number;
  lower: number;
  upper: number;
  votes: number;
  preliminary: boolean;
}

function heatmapCells(categories: CategoryBoard[]): Cell[] {
  return categories.flatMap(({ category, standings }) =>
    standings.map(({ system, score, interval, votes, preliminary }) => ({
      system,
      category,
      score,
      lower: interval.lower,
      upper: interval.upper,
      votes,
      preliminary,
    })),
  );
}

interface CategoryHeatmapProps {
  categories: CategoryBoard[];
  /** Systems from top to bottom, in the leaderboard's order. */
  systems: string[];
}

function scoreScale(cells: Cell[]) {
  const scores = cells.map((cell) => cell.score);
  return Plot.scale({
    color: {
      type: 'quantize',
      n: STEPS,
      domain: [Math.min(...scores), Math.max(...scores)],
      range: Array.from({ length: STEPS }, (_, i) => `var(--chart-seq-${i + 1})`),
    },
  });
}

/** Returns the 1-based ramp step of a score under the scale's thresholds. */
function step(scale: Plot.Scale, score: number): number {
  const thresholds = (scale.domain ?? []) as number[];
  return 1 + thresholds.filter((threshold) => score >= threshold).length;
}

function Legend({ scale }: { scale: Plot.Scale }) {
  const thresholds = (scale.domain ?? []) as number[];
  const colors = (scale.range ?? []) as string[];
  const labels = colors.map((_, i) =>
    i === 0
      ? `< ${formatScore(thresholds[0])}`
      : i === thresholds.length
        ? `≥ ${formatScore(thresholds[i - 1])}`
        : `${formatScore(thresholds[i - 1])}–${formatScore(thresholds[i])}`,
  );
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground tabular-nums">
      {colors.map((color, i) => (
        <li key={color} className="inline-flex items-center gap-1.5">
          <span className="size-3 rounded-sm" style={{ background: color }} aria-hidden="true" />
          {labels[i]}
        </li>
      ))}
      <li className="inline-flex items-center gap-1.5">
        <svg className="size-3 rounded-sm" aria-hidden="true">
          <rect width="12" height="12" fill="var(--chart-seq-3)" />
          <rect width="12" height="12" fill={`url(#${HATCH})`} />
          {/* The heatmap's preliminary cells fill with this pattern too. */}
          <defs>
            <pattern
              id={HATCH}
              width="8"
              height="8"
              patternUnits="userSpaceOnUse"
              patternTransform="rotate(45)"
            >
              <line
                x1="0"
                y1="0"
                x2="0"
                y2="8"
                stroke="var(--background)"
                strokeWidth="1.5"
                strokeOpacity="0.4"
              />
            </pattern>
          </defs>
        </svg>
        Preliminary
      </li>
    </ul>
  );
}

/** Per-category scores of each system in one sequential hue, with vote counts in each cell. */
export function CategoryHeatmap({ categories, systems }: CategoryHeatmapProps) {
  const cells = useMemo(() => heatmapCells(categories), [categories]);
  const scale = useMemo(() => scoreScale(cells), [cells]);
  const height = systems.length * ROW_HEIGHT + 40;

  const plot = useCallback(
    ({ measure }: PlotFrame): Plot.PlotOptions => {
      const fill = (cell: Cell) => `var(--chart-seq-${step(scale, cell.score)})`;
      const ink = (cell: Cell) => `var(--chart-seq-ink-${step(scale, cell.score)})`;
      return {
        height,
        marginLeft: Math.max(...systems.map(measure)) + LABEL_GAP,
        marginTop: 28,
        marginBottom: 12,
        x: { axis: 'top', label: null, tickSize: 0, padding: 0 },
        y: { domain: systems, label: null, tickSize: 0, padding: 0 },
        color: { type: 'identity' },
        marks: [
          Plot.cell(cells, { x: 'category', y: 'system', fill, inset: 1, rx: 4 }),
          Plot.cell(
            cells.filter((cell) => cell.preliminary),
            { x: 'category', y: 'system', fill: `url(#${HATCH})`, inset: 1, rx: 4 },
          ),
          // A solid patch keeps the hatch to a frame around the labels, which keep their contrast.
          Plot.cell(
            cells.filter((cell) => cell.preliminary),
            {
              x: 'category',
              y: 'system',
              fill,
              insetTop: 5,
              insetBottom: 5,
              insetLeft: 10,
              insetRight: 10,
              rx: 3,
            },
          ),
          Plot.text(cells, {
            x: 'category',
            y: 'system',
            text: (cell: Cell) => formatScore(cell.score),
            fill: ink,
            fontWeight: 600,
            dy: -6,
          }),
          Plot.text(cells, {
            x: 'category',
            y: 'system',
            text: (cell: Cell) => `${cell.votes} votes`,
            fill: ink,
            fontSize: 10,
            dy: 8,
          }),
          Plot.tip(
            cells,
            Plot.pointer({
              x: 'category',
              y: 'system',
              channels: {
                Score: (cell: Cell) => formatScore(cell.score),
                Interval: (cell: Cell) => formatInterval(cell),
                Votes: 'votes',
                Status: (cell: Cell) => (cell.preliminary ? 'Preliminary' : 'Settled'),
              },
              format: { x: true, y: true },
              fontFamily: 'inherit',
            }),
          ),
        ],
      };
    },
    [cells, scale, systems, height],
  );

  return (
    <div className="space-y-3">
      <div className="overflow-x-auto">
        <PlotFigure plot={plot} height={height} className="min-w-[32rem]" />
      </div>
      <Legend scale={scale} />
    </div>
  );
}
