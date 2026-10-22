import { getCollection } from 'astro:content';

import type { FamilyConfig } from '@/lib/family';
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
  config: FamilyConfig;
  /**
   * Closed rounds, oldest first, so the last one carries the cumulative board. Empty until the
   * first round closes.
   */
  rounds: Report[];
}

/**
 * Pairs each public family's config with its reports, by name. Families without `public = true`
 * stay off the site together with their reports.
 */
export function listFamilies(
  configs: { name: string; config: FamilyConfig }[],
  reports: Report[],
): Family[] {
  const rounds = Map.groupBy(reports, (report) => report.family);
  return configs
    .filter(({ config }) => config.public)
    .map(({ name, config }) => ({
      name,
      config,
      rounds: (rounds.get(name) ?? []).toSorted(
        (a, b) => Date.parse(a.closed_at) - Date.parse(b.closed_at),
      ),
    }))
    .toSorted((a, b) => a.name.localeCompare(b.name));
}

export async function getFamilies(): Promise<Family[]> {
  const [configs, reports] = await Promise.all([
    getCollection('families'),
    getCollection('reports'),
  ]);
  return listFamilies(
    configs.map((entry) => ({ name: entry.id, config: entry.data })),
    reports.map((entry) => entry.data),
  );
}

/** Returns a family's latest round, or nothing before its first round closes. */
export function latest(family: Family): Report | undefined {
  return family.rounds.at(-1);
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
