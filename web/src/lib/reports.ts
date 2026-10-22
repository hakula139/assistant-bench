import { getCollection } from 'astro:content';

import { aspectIds, familyTitle, type Aspect } from '@/lib/aspects';
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
  /** The family's full name, which titles and lists outside its aspect show. */
  title: string;
  /** The aspect the family is listed under, which every public family has. */
  aspect: Aspect;
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
 * Pairs each public family's config with its reports, by name, in aspect order and then by name.
 * Families without `public = true` stay off the site together with their reports.
 *
 * @throws Error when two families, listed or not, share a full name, or a public family has no
 *   aspect.
 */
export function listFamilies(
  configs: { name: string; config: FamilyConfig }[],
  reports: Report[],
  systemNames: SystemNames,
): Family[] {
  const owners = new Map<string, string>();
  for (const { name, config } of configs) {
    const title = familyTitle(config);
    const owner = owners.get(title);
    if (owner !== undefined) {
      throw new Error(`families ${owner} and ${name} share the name ${title}`);
    }
    owners.set(title, name);
  }
  const rounds = Map.groupBy(reports, (report) => report.family);
  return configs
    .filter(({ config }) => config.public)
    .map(({ name, config }) => {
      if (config.aspect === undefined) {
        throw new Error(`public family ${name} has no aspect`);
      }
      return {
        name,
        title: familyTitle(config),
        aspect: config.aspect,
        config,
        rounds: (rounds.get(name) ?? []).toSorted(
          (a, b) => Date.parse(a.closed_at) - Date.parse(b.closed_at),
        ),
        systemNames,
      };
    })
    .toSorted(
      (a, b) =>
        aspectIds.indexOf(a.aspect) - aspectIds.indexOf(b.aspect) || a.name.localeCompare(b.name),
    );
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

/** Returns the listed families grouped by aspect, in `listFamilies` order. */
export function byAspect(families: Family[]): { aspect: Aspect; families: Family[] }[] {
  return [...Map.groupBy(families, (family) => family.aspect)].map(([aspect, members]) => ({
    aspect,
    families: members,
  }));
}

/** Returns the systems with a page on a family's board: those its voted rounds placed. */
export function systemPages(family: Family): Set<string> {
  return new Set(
    votesRounds(family).flatMap(({ results }) => results.standings.map((s) => s.system)),
  );
}

/** A link to one of an aspect's boards, where `current` marks the board on screen. */
export interface BoardLink {
  href: string;
  label: string;
  current: boolean;
}

/** Returns the families listed under `family`'s aspect, itself included, in `listFamilies` order. */
function siblings(families: Family[], family: Family): Family[] {
  return Map.groupBy(families, (item) => item.aspect).get(family.aspect) ?? [];
}

/** Returns links to the boards of `family`'s aspect by their short names. */
export function familyBoards(families: Family[], family: Family): BoardLink[] {
  return siblings(families, family).map((item) => ({
    href: `/${item.name}/`,
    label: item.config.display_name,
    current: item === family,
  }));
}

/** Returns links to `system`'s page on each board of `family`'s aspect that gives it one. */
export function systemBoards(families: Family[], family: Family, system: string): BoardLink[] {
  return siblings(families, family)
    .filter((item) => systemPages(item).has(system))
    .map((item) => ({
      href: `/${item.name}/${system}/`,
      label: item.config.display_name,
      current: item === family,
    }));
}
