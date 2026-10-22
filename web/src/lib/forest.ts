import { nice, tickStep, ticks } from 'd3-array';

import type { Interval, Standing } from '@/lib/report';

/** A score with its 95% interval, as a forest plot draws it. */
export interface Estimate {
  score: number;
  interval: Interval;
}

/** The score every family's and category's reference system is anchored at. */
export const REFERENCE_SCORE = 1000;

/**
 * How far below the top score an axis may start. At 600 points the top system would win about
 * 97% of the votes between the two, so a system further down is out of contention and sits at the
 * axis's start.
 */
export const AXIS_SPAN_BELOW_TOP = 600;

/** The axis of a family with no closed round: its reference at 1,000 and room on either side. */
export const EMPTY_DOMAIN: [number, number] = [600, 1400];

const TICKS = 5;

/**
 * Returns the x domain of one board's forest plot, shared by every row: every interval's upper
 * end, and every lower end down to `AXIS_SPAN_BELOW_TOP` below the top score, rounded outward to
 * round ticks.
 */
export function forestDomain(estimates: Estimate[]): [number, number] {
  if (estimates.length === 0) {
    return EMPTY_DOMAIN;
  }
  const top = Math.max(...estimates.map((estimate) => estimate.score));
  const low = Math.max(
    Math.min(...estimates.map((estimate) => estimate.interval.lower)),
    top - AXIS_SPAN_BELOW_TOP,
  );
  const high = Math.max(...estimates.map((estimate) => estimate.interval.upper));
  const [start, stop] = nice(low, high, TICKS);
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
 * Returns the ticks a narrow forest plot labels: both ends, and the reference when it sits far
 * enough from both that the labels cannot overlap.
 */
export function sparseTicks(domain: [number, number]): number[] {
  const all = forestTicks(domain);
  const [start, stop] = domain;
  const share = (REFERENCE_SCORE - start) / (stop - start);
  const reference = all.includes(REFERENCE_SCORE) && share >= 0.25 && share <= 0.75;
  return [all[0], ...(reference ? [REFERENCE_SCORE] : []), all[all.length - 1]];
}

/** The plot width, in CSS pixels, below which an axis labels only its sparse ticks. */
const NARROW_AXIS = 240;

/** Returns the ticks a forest plot of a given width, in CSS pixels, labels. */
export function labelledTicks(domain: [number, number], width: number): number[] {
  return width < NARROW_AXIS ? sparseTicks(domain) : forestTicks(domain);
}

/** Where an estimate falls on an axis, with what the axis start cuts off. */
export interface Clipped {
  /** The score, or the axis start when the score lies below it. */
  score: number;
  lower: number;
  upper: number;
  /** Whether the score itself lies below the axis start. */
  offScale: boolean;
  /** Whether the interval runs past the axis start. */
  cutLow: boolean;
}

/** Clips an estimate to an axis from `forestDomain`, which never cuts the top end. */
export function clip({ score, interval }: Estimate, [start]: [number, number]): Clipped {
  return {
    score: Math.max(score, start),
    lower: Math.max(interval.lower, start),
    upper: Math.max(interval.upper, start),
    offScale: score < start,
    cutLow: interval.lower < start,
  };
}

/**
 * Why a row has no estimate: the reference before any round closes, a system whose votes do not
 * yet connect it to the reference, or a system with no votes.
 */
export type Unplaced = 'reference' | 'unranked' | 'no-votes';

/** One system's row in a static forest plot. */
export type ForestRow = { system: string; href?: string } & (
  { estimate: Estimate; preliminary: boolean } | { unplaced: Unplaced }
);

const byName = (a: string, b: string) => a.localeCompare(b);

/** Rows for a family before its first round closes, in name order. */
export function unmeasuredRows(systems: string[], reference: string): ForestRow[] {
  return systems.toSorted(byName).map((system) => ({
    system,
    unplaced: system === reference ? 'reference' : 'no-votes',
  }));
}

/**
 * Rows for a closed round: its standings from the highest score down, each linked to its system
 * page, then its unranked systems, then the family's systems the round has no votes on.
 */
export function roundRows(
  family: string,
  systems: string[],
  { standings, unranked }: { standings: Standing[]; unranked: string[] },
): ForestRow[] {
  const placed = new Set([...standings.map((standing) => standing.system), ...unranked]);
  return [
    ...standings
      .toSorted((a, b) => b.score - a.score)
      .map((standing) => ({
        system: standing.system,
        href: `/${family}/${standing.system}/`,
        estimate: standing,
        preliminary: standing.preliminary,
      })),
    ...unranked.toSorted(byName).map((system) => ({ system, unplaced: 'unranked' as const })),
    ...systems
      .filter((system) => !placed.has(system))
      .toSorted(byName)
      .map((system) => ({ system, unplaced: 'no-votes' as const })),
  ];
}
