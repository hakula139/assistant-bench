import type { VotesRules } from '@/lib/report';

export function isPreliminary(standing: { decisive_votes: number }, rules: VotesRules): boolean {
  return standing.decisive_votes < rules.preliminary_decisive_votes;
}
