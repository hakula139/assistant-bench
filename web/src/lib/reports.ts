import { getCollection } from 'astro:content';

import { familyTitle, type Aspect } from '@/lib/aspects';
import type { FamilyConfig, Scorer } from '@/lib/family';
import { systemPagePath } from '@/lib/paths';
import type { Report, TestsResults, VotesResults } from '@/lib/report';
import { resolveSystemNames, type SystemNames } from '@/lib/systems';

export interface VotesRound {
  round: string;
  closed_at: string;
  systems: Report['systems'];
  results: VotesResults;
}

export interface TestsRound {
  round: string;
  closed_at: string;
  systems: Report['systems'];
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
  /** Current catalog labels for families without a closed round. */
  systemNames: SystemNames;
}

/**
 * A family's path segment under its aspect: its ID without the aspect prefix.
 *
 * @throws Error when the ID does not start with its aspect and a hyphen.
 */
export function boardSlug(name: string, aspect: Aspect): string {
  const prefix = `${aspect}-`;
  if (!name.startsWith(prefix)) {
    throw new Error(
      `family ${name}: a public family ID starts with its aspect, as in ${aspect}-<name>`,
    );
  }

  return name.slice(prefix.length);
}

function boardParams(family: Family): { aspect: Aspect; board: string } {
  return { aspect: family.aspect, board: boardSlug(family.name, family.aspect) };
}

/** A family's board path, which every link to the board and its system pages starts with. */
export function boardPath(family: Family): string {
  const { aspect, board } = boardParams(family);
  return `/${aspect}/${board}/`;
}

/** A closed round's page path on a family's board. */
export function roundPath(family: Family, round: string): string {
  return `${boardPath(family)}rounds/${round}/`;
}

/** A system's page path on a family's board. */
export function systemPath(family: Family, system: string): string {
  return systemPagePath(boardPath(family), system);
}

/**
 * Pairs each public family's config with its reports, by name, and lists them by name. Families
 * without `public = true` stay off the site together with their reports.
 *
 * @throws Error when two families, listed or not, share a full name, or when a public family has
 *   no aspect.
 */
export function publicFamilies(
  configs: { name: string; config: FamilyConfig }[],
  reports: Report[],
  systemNames: SystemNames,
): Family[] {
  const owners = new Map<string, string>();
  for (const { name, config } of configs) {
    const title = familyTitle(config);
    const owner = owners.get(title);
    if (owner !== undefined) {
      throw new Error(`families ${owner} and ${name} share the full name ${title}`);
    }
    owners.set(title, name);
  }

  const rounds = Map.groupBy(reports, (report) => report.family);

  return configs
    .flatMap(({ name, config }) => {
      if (!config.public) {
        return [];
      }

      if (config.aspect === undefined) {
        throw new Error(`public family ${name} has no aspect`);
      }

      return [
        {
          name,
          title: familyTitle(config),
          aspect: config.aspect,
          config,
          rounds: (rounds.get(name) ?? []).toSorted(
            (a, b) => Date.parse(a.closed_at) - Date.parse(b.closed_at),
          ),
          systemNames,
        },
      ];
    })
    .toSorted((a, b) => a.name.localeCompare(b.name));
}

export async function getFamilies(): Promise<Family[]> {
  const [configs, reports, systems, models] = await Promise.all([
    getCollection('families'),
    getCollection('reports'),
    getCollection('systems'),
    getCollection('models'),
  ]);

  return publicFamilies(
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

/** Returns a round's fields that its board shows when votes scored it, or nothing otherwise. */
export function votesRound({ round, closed_at, systems, results }: Report): VotesRound | undefined {
  return results.scorer === 'votes' ? { round, closed_at, systems, results } : undefined;
}

/** Returns a round's fields that its board shows when task tests scored it, or nothing otherwise. */
export function testsRound({ round, closed_at, systems, results }: Report): TestsRound | undefined {
  return results.scorer === 'tests' ? { round, closed_at, systems, results } : undefined;
}

/** Returns the rounds of a family scored by votes, oldest first. */
export function votesRounds(family: Family): VotesRound[] {
  return family.rounds.flatMap((report) => votesRound(report) ?? []);
}

/** Returns the rounds of a family scored by task tests, oldest first. */
export function testsRounds(family: Family): TestsRound[] {
  return family.rounds.flatMap((report) => testsRound(report) ?? []);
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

/** A link to one of a family's closed rounds, which the board's Round filter navigates to. */
export interface RoundLink {
  round: string;
  href: string;
}

/** Returns links to a family's closed rounds, newest first, where the latest links the board. */
export function roundLinks(family: Family): RoundLink[] {
  return family.rounds.toReversed().map(({ round }, index) => ({
    round,
    href: index === 0 ? boardPath(family) : roundPath(family, round),
  }));
}

/** Returns links to the boards of each family's aspect, keyed by family name. */
function aspectBoards(families: Family[]): Map<string, BoardLink[]> {
  const byAspect = Map.groupBy(families, (family) => family.aspect);
  return new Map(
    families.map((family) => [
      family.name,
      (byAspect.get(family.aspect) ?? []).map((item): BoardLink => ({
        href: boardPath(item),
        label: item.config.display_name,
        current: item.name === family.name,
      })),
    ]),
  );
}

/** Returns each family page's params and props: the family and its aspect's boards. */
export function familyPaths(families: Family[]) {
  const boards = aspectBoards(families);
  return families.map((family) => ({
    params: boardParams(family),
    props: { family, boards: boards.get(family.name) ?? [] },
  }));
}

/**
 * Returns each round page's params and props: the family, its aspect's boards, and the closed
 * round the page shows.
 */
export function roundPaths(families: Family[]) {
  const boards = aspectBoards(families);
  return families.flatMap((family) =>
    family.rounds.map((round) => ({
      params: { ...boardParams(family), round: round.round },
      props: { family, boards: boards.get(family.name) ?? [], round },
    })),
  );
}

/**
 * Returns each system page's params and props: the family, the system, and the system's pages on
 * the boards of the family's aspect that give it one.
 */
export function systemPaths(families: Family[]) {
  const pages = new Map(families.map((family) => [family.name, systemPages(family)]));
  const byAspect = Map.groupBy(families, (family) => family.aspect);
  return families.flatMap((family) =>
    [...systemPages(family)].map((system) => ({
      params: { ...boardParams(family), system },
      props: {
        family,
        system,
        boards: (byAspect.get(family.aspect) ?? [])
          .filter((item) => pages.get(item.name)?.has(system))
          .map((item): BoardLink => ({
            href: systemPath(item, system),
            label: item.config.display_name,
            current: item.name === family.name,
          })),
      },
    })),
  );
}
