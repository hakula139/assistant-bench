import { getCollection } from 'astro:content';

import type { FamilyConfig, Scorer } from '@/lib/family';
import type { Report, TestsResults, VotesResults } from '@/lib/report';
import { resolveSystemNames, type SystemNames } from '@/lib/systems';

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
  /** Every system's display name, which pages show wherever the reports name a system. */
  systemNames: SystemNames;
}

/**
 * Pairs each public family's config with its reports, by name. Families without `public = true`
 * stay off the site together with their reports.
 *
 * @throws Error when two families, listed or not, share a display name.
 */
export function listFamilies(
  configs: { name: string; config: FamilyConfig }[],
  reports: Report[],
  systemNames: SystemNames,
): Family[] {
  const owners = new Map<string, string>();
  for (const { name, config } of configs) {
    const owner = owners.get(config.display_name);
    if (owner !== undefined) {
      throw new Error(
        `families ${owner} and ${name} share the display name ${config.display_name}`,
      );
    }
    owners.set(config.display_name, name);
  }
  const rounds = Map.groupBy(reports, (report) => report.family);
  return configs
    .filter(({ config }) => config.public)
    .map(({ name, config }) => ({
      name,
      config,
      rounds: (rounds.get(name) ?? []).toSorted(
        (a, b) => Date.parse(a.closed_at) - Date.parse(b.closed_at),
      ),
      systemNames,
    }))
    .toSorted((a, b) => a.name.localeCompare(b.name));
}

export async function getFamilies(): Promise<Family[]> {
  const [configs, reports, systems, models] = await Promise.all([
    getCollection('families'),
    getCollection('reports'),
    getCollection('systems'),
    getCollection('models'),
  ]);
  return listFamilies(
    configs.map((entry) => ({ name: entry.id, config: entry.data })),
    reports.map((entry) => entry.data),
    resolveSystemNames(systems, new Map(models.map((entry) => [entry.id, entry.data.name]))),
  );
}

/**
 * Returns the config of a family that the caller dispatched by scorer, typed for that scorer.
 *
 * @throws Error when the family is scored otherwise, which breaks the dispatch's contract.
 */
export function configOf<S extends Scorer>(
  family: Family,
  scorer: S,
): Extract<FamilyConfig, { scorer: S }> {
  const { config } = family;
  if (config.scorer !== scorer) {
    throw new Error(`expected ${family.name} to be scored by ${scorer}, got ${config.scorer}`);
  }
  return config as Extract<FamilyConfig, { scorer: S }>;
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
