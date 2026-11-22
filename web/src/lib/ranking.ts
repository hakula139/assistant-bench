import type { PassRate, ScoreInterval, VotesMethod, VotesResults, VotesRules } from '@/lib/report';

/** Whether a score has no interval or one wider than the rule allows. */
export function isPreliminary(interval: ScoreInterval | null, rules?: VotesRules | null): boolean {
  return (
    interval === null ||
    !rules ||
    (interval.upper - interval.lower) / 2 > rules.preliminary_half_width
  );
}

export const NET_DOMAIN: [number, number] = [-100, 100];

/** The fixed axis marker only belongs to the historical Elo scale. */
export function fixedAnchor(method: VotesMethod): number | undefined {
  return 'kind' in method ? undefined : method.anchor_score;
}

/** Display expected net points on a per-100-comparisons scale. */
export function displayVoteScores(results: VotesResults): VotesResults {
  if (!('kind' in results.method)) return results;

  const scale = <T extends { score: number }>(row: T): T => ({ ...row, score: 100 * row.score });
  return {
    ...results,
    standings: results.standings.map(scale),
    categories: results.categories.map((board) => ({
      ...board,
      standings: board.standings.map(scale),
    })),
  };
}

/** Orders systems by pass rate, highest first, and then by display name. */
export function byPassRate(
  a: { name: string; pass_rate: PassRate },
  b: { name: string; pass_rate: PassRate },
): number {
  return b.pass_rate.rate - a.pass_rate.rate || a.name.localeCompare(b.name);
}

/** Matches the frozen checker standings' identifier tie-break. */
export function byCheckersPassRate(
  a: { system: string; pass_rate: PassRate },
  b: { system: string; pass_rate: PassRate },
): number {
  return b.pass_rate.rate - a.pass_rate.rate || a.system.localeCompare(b.system);
}
