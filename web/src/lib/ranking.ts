import type { PassRate, VotesRules } from '@/lib/report';

export function isPreliminary(standing: { decisive_votes: number }, rules: VotesRules): boolean {
  return standing.decisive_votes < rules.preliminary_decisive_votes;
}

/** Orders systems by pass rate, highest first, and then by display name. */
export function byPassRate(
  a: { name: string; pass_rate: PassRate },
  b: { name: string; pass_rate: PassRate },
): number {
  return b.pass_rate.rate - a.pass_rate.rate || a.name.localeCompare(b.name);
}
