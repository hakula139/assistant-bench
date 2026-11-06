import type { PassRate, ScoreInterval, VotesRules } from '@/lib/report';

/** Whether a score has no interval, as a category refit does, or one wider than the rule allows. */
export function isPreliminary(interval: ScoreInterval | null, rules: VotesRules): boolean {
  return interval === null || (interval.upper - interval.lower) / 2 > rules.preliminary_half_width;
}

/** Orders systems by pass rate, highest first, and then by display name. */
export function byPassRate(
  a: { name: string; pass_rate: PassRate },
  b: { name: string; pass_rate: PassRate },
): number {
  return b.pass_rate.rate - a.pass_rate.rate || a.name.localeCompare(b.name);
}
