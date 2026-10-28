import { nice, ticks, tickStep } from 'd3-array';

import { isPreliminary } from '@/lib/ranking';
import type { Standing, VotesRules } from '@/lib/report';
import { systemName, type SystemNames } from '@/lib/systems';

export interface Estimate {
  score: number;
}

/** Maximum visible gap below the top point, in score points. */
export const AXIS_SPAN_BELOW_TOP = 600;

/** Presentation space for a family with no measured scores. */
export const EMPTY_DOMAIN: [number, number] = [600, 1400];

const TICKS = 5;

/**
 * Clips the low end below the top point and keeps ticks at whole-score resolution. Equal points
 * use the empty plot's display span centered on their score.
 */
export function forestDomain(estimates: Estimate[]): [number, number] {
  if (estimates.length === 0) {
    return EMPTY_DOMAIN;
  }

  const top = Math.max(...estimates.map((estimate) => estimate.score));
  const low = Math.max(
    Math.min(...estimates.map((estimate) => estimate.score)),
    top - AXIS_SPAN_BELOW_TOP,
  );
  const high = top;

  if (low === high) {
    const halfSpan = (EMPTY_DOMAIN[1] - EMPTY_DOMAIN[0]) / 2;
    return [low - halfSpan, high + halfSpan];
  }

  const padding = Math.max(0, (TICKS - (high - low)) / 2);
  const [start, stop] = nice(low - padding, high + padding, TICKS);

  return [start, stop];
}

/**
 * Returns one axis for several boards, such as a round's categories, from the lowest board's
 * `forestDomain` start to the highest one's stop, so that every board keeps its own cut below its
 * own top score.
 */
export function sharedDomain(boards: Estimate[][]): [number, number] {
  const domains = boards.filter((board) => board.length > 0).map(forestDomain);
  if (domains.length === 0) {
    return EMPTY_DOMAIN;
  }

  return [
    Math.min(...domains.map(([start]) => start)),
    Math.max(...domains.map(([, stop]) => stop)),
  ];
}

/**
 * Returns the values a forest plot draws gridlines at: both domain ends, which a `sharedDomain`
 * may leave off the round step, and the round values between them that sit at least half a step
 * from either end, so no two labels crowd.
 */
export function forestTicks([start, stop]: [number, number]): number[] {
  const step = tickStep(start, stop, TICKS);
  const inner = ticks(start, stop, TICKS).filter(
    (tick) => tick - start >= step / 2 && stop - tick >= step / 2,
  );
  return [start, ...inner, stop];
}

/**
 * Returns the ticks a narrow forest plot labels: both ends, and the anchor when it sits far enough
 * from both that the labels cannot overlap.
 */
export function sparseTicks(domain: [number, number], anchorScore?: number): number[] {
  const all = forestTicks(domain);
  const [start, stop] = domain;
  const share = anchorScore === undefined ? 0 : (anchorScore - start) / (stop - start);
  const anchor =
    anchorScore !== undefined && all.includes(anchorScore) && share >= 0.25 && share <= 0.75;
  return [all[0], ...(anchor ? [anchorScore] : []), all[all.length - 1]];
}

/**
 * The axis width the narrowest interval between ticks needs, in CSS pixels, for every tick to keep
 * its label apart from its neighbors'. The `axisFits` variants in `ScoreTrack.tsx` match it.
 */
const INTERVAL_WIDTH = 40;

/**
 * Returns how many of the narrowest tick intervals span the axis, rounded up, so that labelling
 * every tick needs `INTERVAL_WIDTH` pixels for each. The end intervals can be as short as half
 * the others.
 */
export function axisIntervals(domain: [number, number]): number {
  const all = forestTicks(domain);
  const gap = Math.min(...all.slice(1).map((tick, i) => tick - all[i]));
  return Math.ceil((domain[1] - domain[0]) / gap);
}

/** Returns the ticks a forest plot labels on an axis `width` CSS pixels wide. */
export function labelledTicks(
  domain: [number, number],
  width: number,
  anchorScore?: number,
): number[] {
  return width >= axisIntervals(domain) * INTERVAL_WIDTH
    ? forestTicks(domain)
    : sparseTicks(domain, anchorScore);
}

/** Where an estimate falls on an axis, with what the axis start cuts off. */
export interface Clipped {
  /** The score, or the axis start when the score lies below it. */
  score: number;
  /** Whether the score itself lies below the axis start. */
  offScale: boolean;
}

/** Whether a reference rule at `value`, when set, lies within the axis domain and can be drawn. */
export const onAxis = ([start, stop]: [number, number], value?: number): value is number =>
  value !== undefined && start <= value && value <= stop;

/**
 * The size of the arrowhead for a score below the axis start, in CSS pixels. Every forest draws it
 * pointing left with its tip on the start gridline.
 */
export const ARROWHEAD = { width: 10, height: 12 };

/** Clips an estimate to an axis from `forestDomain`, which never cuts the top end. */
export function clip({ score }: Estimate, [start]: [number, number]): Clipped {
  return {
    score: Math.max(score, start),
    offScale: score < start,
  };
}

/**
 * Why a row has no estimate: the pre-round anchor, a system whose votes do not yet connect it to
 * the anchor, or a system with no votes.
 */
type Unplaced = 'anchor' | 'unranked' | 'no-votes';

/** What a forest prints in the row of a system it cannot place. */
export const unplacedText: Record<Unplaced, string> = {
  'anchor': 'no votes',
  'unranked': 'unranked',
  'no-votes': 'no votes',
};

/** One system's row in a static forest plot, labelled by its display `name`. */
export type ForestRow = { system: string; name: string; href?: string } & (
  { estimate: Estimate; preliminary: boolean } | { unplaced: Unplaced }
);

const byName = (names: SystemNames) => (a: string, b: string) =>
  systemName(names, a).localeCompare(systemName(names, b));

/** Rows for a family before its first round closes, in display name order. */
export function unmeasuredRows(systems: string[], anchor: string, names: SystemNames): ForestRow[] {
  return systems.toSorted(byName(names)).map((system) => ({
    system,
    name: systemName(names, system),
    unplaced: system === anchor ? 'anchor' : 'no-votes',
  }));
}

/** Closed-round rows use only the report's standings and unranked systems. */
export function roundRows(
  href: (system: string) => string,
  { standings, unranked }: { standings: Standing[]; unranked: string[] },
  names: SystemNames,
  rules: VotesRules,
): ForestRow[] {
  const unplaced = (system: string, reason: Unplaced) => ({
    system,
    name: systemName(names, system),
    unplaced: reason,
  });

  return [
    ...standings
      .toSorted((a, b) => b.score - a.score)
      .map((standing) => ({
        system: standing.system,
        name: systemName(names, standing.system),
        href: href(standing.system),
        estimate: standing,
        preliminary: isPreliminary(standing, rules),
      })),
    ...unranked.toSorted(byName(names)).map((system) => unplaced(system, 'unranked')),
  ];
}

/** A mark a forest plot draws, which its legend names. */
export type LegendMark = 'score' | 'preliminary' | 'cut' | 'anchor';

const LEGEND_ORDER: LegendMark[] = ['score', 'preliminary', 'cut', 'anchor'];

/**
 * Returns the marks a forest plot of `rows` on `domain` draws, in legend order. The plot rules
 * `anchorScore` only where it lies on the axis.
 */
export function legendMarks(
  rows: ForestRow[],
  domain: [number, number],
  anchorScore?: number,
): LegendMark[] {
  const drawn = new Set<LegendMark>(onAxis(domain, anchorScore) ? ['anchor'] : []);

  for (const row of rows) {
    if ('estimate' in row) {
      const clipped = clip(row.estimate, domain);

      if (clipped.offScale) {
        drawn.add('cut');
      } else {
        drawn.add(row.preliminary ? 'preliminary' : 'score');
      }
    }
  }

  return LEGEND_ORDER.filter((mark) => drawn.has(mark));
}

/** Returns the marks of several plots that share one legend, in legend order. */
export function unionMarks(plots: LegendMark[][]): LegendMark[] {
  const drawn = new Set(plots.flat());
  return LEGEND_ORDER.filter((mark) => drawn.has(mark));
}
