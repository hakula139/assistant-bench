import { getCollection } from 'astro:content';

import type { Report, TestsResults, VotesResults } from '@/lib/report';

export interface VotesRound {
  round: string;
  results: VotesResults;
}

export interface TestsRound {
  round: string;
  results: TestsResults;
}

export interface Family {
  name: string;
  /** Closed rounds, oldest first, so the last one carries the cumulative board. */
  rounds: Report[];
}

export async function getFamilies(): Promise<Family[]> {
  const reports = await getCollection('reports');
  const families = Map.groupBy(
    reports.map((entry) => entry.data),
    (report) => report.family,
  );
  return [...families]
    .map(([name, rounds]) => ({
      name,
      rounds: rounds.toSorted((a, b) => Date.parse(a.closed_at) - Date.parse(b.closed_at)),
    }))
    .toSorted((a, b) => a.name.localeCompare(b.name));
}

/** Returns a family's latest round, which every family has. */
export function latest(family: Family): Report {
  return family.rounds[family.rounds.length - 1];
}

/** Returns the rounds of a family scored by votes, oldest first. */
export function votesRounds(family: Family): VotesRound[] {
  return family.rounds.flatMap(({ round, results }) =>
    results.scorer === 'votes' ? [{ round, results }] : [],
  );
}

/** Returns the rounds of a family scored by task tests, oldest first. */
export function testsRounds(family: Family): TestsRound[] {
  return family.rounds.flatMap(({ round, results }) =>
    results.scorer === 'tests' ? [{ round, results }] : [],
  );
}
