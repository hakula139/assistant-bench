import { nice, ticks } from 'd3-array';

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
 * Returns a forest plot's x domain, shared by every row: every interval's upper end, and every
 * lower end down to `AXIS_SPAN_BELOW_TOP` below the top score, rounded outward to round ticks.
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

/** Returns round tick values across a domain. */
export function forestTicks([start, stop]: [number, number], count = TICKS): number[] {
  return ticks(start, stop, count);
}

/** Where an estimate falls on a domain, with what the axis cuts off. */
export interface Clipped {
  /** The score, or the axis start when the score lies below it. */
  score: number;
  lower: number;
  upper: number;
  /** Whether the score itself lies below the axis start. */
  offScale: boolean;
  /** Whether the interval runs past the axis start. */
  cutLow: boolean;
  /** Whether the interval runs past the axis end. */
  cutHigh: boolean;
}

export function clip({ score, interval }: Estimate, [start, stop]: [number, number]): Clipped {
  return {
    score: Math.max(score, start),
    lower: Math.max(interval.lower, start),
    upper: Math.min(Math.max(interval.upper, start), stop),
    offScale: score < start,
    cutLow: interval.lower < start,
    cutHigh: interval.upper > stop,
  };
}

/** One system's row in a static forest plot. */
export interface ForestRow {
  system: string;
  href?: string;
  /** Missing until a closed round bounds the system, which draws its interval unbounded. */
  estimate?: Estimate;
  preliminary?: boolean;
  /** Marks the family's reference, which sits at 1,000 before any round closes. */
  reference?: boolean;
}

/** Rows for a family before its first round closes, in name order. */
export function unmeasuredRows(systems: string[], reference: string): ForestRow[] {
  return systems
    .toSorted((a, b) => a.localeCompare(b))
    .map((system) => ({ system, reference: system === reference }));
}

/** Rows for a round's standings, from the highest score down, each linked to its system page. */
export function standingRows(family: string, standings: Standing[]): ForestRow[] {
  return standings
    .toSorted((a, b) => b.score - a.score)
    .map((standing) => ({
      system: standing.system,
      href: `/${family}/${standing.system}/`,
      estimate: standing,
      preliminary: standing.preliminary,
    }));
}
