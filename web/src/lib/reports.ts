import { getCollection } from 'astro:content';

import { familyTitle, type Aspect } from '@/lib/aspects';
import type { FamilyConfig, Scorer } from '@/lib/family';
import { systemPagePath } from '@/lib/paths';
import type { Report } from '@/lib/report';
import { sitePath } from '@/lib/site';
import type { SystemNames } from '@/lib/systems';

/** A report whose results the scorer `S` wrote. */
export type ScoredReport<S extends Scorer> = Report & {
  results: Extract<Report['results'], { scorer: S }>;
};

export type VotesReport = ScoredReport<'votes'>;

export type TestsReport = ScoredReport<'tests'>;
export type CheckersReport = ScoredReport<'checkers'>;

/** A public family scored by `S`, whose config and rounds that scorer types. */
interface ScoredFamily<S extends Scorer> {
  name: string;
  /** The family's full name, which titles and lists outside its aspect show. */
  title: string;
  /** The aspect the family is listed under, which every public family has. */
  aspect: Aspect;
  scorer: S;
  config: Extract<FamilyConfig, { scorer: S }>;
  /**
   * Closed rounds, oldest first, so the last one carries the cumulative board. Empty until the
   * first round closes.
   */
  rounds: ScoredReport<S>[];
  /** Current display names for families without a closed round. */
  systemNames: SystemNames;
}

export type VotesFamily = ScoredFamily<'votes'>;

export type TestsFamily = ScoredFamily<'tests'>;
export type CheckersFamily = ScoredFamily<'checkers'>;

export type Family = VotesFamily | TestsFamily | CheckersFamily;

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

export function boardPath(family: Family): string {
  const { aspect, board } = boardParams(family);
  return sitePath(`/${aspect}/${board}/`);
}

export function roundPath(family: Family, round: string): string {
  return `${boardPath(family)}rounds/${round}/`;
}

export function systemPath(family: Family, system: string): string {
  return systemPagePath(boardPath(family), system);
}

/** Returns `reports`, oldest first, once each holds results that `scorer` wrote. */
function scoredRounds<S extends Scorer>(scorer: S, reports: Report[]): ScoredReport<S>[] {
  for (const { family, round, results } of reports) {
    if (results.scorer !== scorer) {
      throw new Error(`report ${family}/${round} is scored by ${results.scorer}, not ${scorer}`);
    }
  }
  return (reports as ScoredReport<S>[]).toSorted(
    (a, b) => Date.parse(a.closed_at) - Date.parse(b.closed_at),
  );
}

/**
 * Pairs each public family's config with its reports, by name, and lists them by name. Families
 * without `public = true` stay off the site together with their reports.
 *
 * @throws Error when two families, listed or not, share a full name, when a public family has no
 *   aspect, or when a public family's report was scored by another scorer.
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
    .flatMap(({ name, config }): Family[] => {
      if (!config.public) {
        return [];
      }

      if (config.aspect === undefined) {
        throw new Error(`public family ${name} has no aspect`);
      }

      const listing = { name, title: familyTitle(config), aspect: config.aspect, systemNames };
      const reports = rounds.get(name) ?? [];
      return [
        config.scorer === 'votes'
          ? { ...listing, scorer: 'votes', config, rounds: scoredRounds('votes', reports) }
          : config.scorer === 'tests'
            ? { ...listing, scorer: 'tests', config, rounds: scoredRounds('tests', reports) }
            : { ...listing, scorer: 'checkers', config, rounds: scoredRounds('checkers', reports) },
      ];
    })
    .toSorted((a, b) => a.name.localeCompare(b.name));
}

export async function getFamilies(): Promise<Family[]> {
  const [configs, reports, systems] = await Promise.all([
    getCollection('families'),
    getCollection('reports'),
    getCollection('systems'),
  ]);

  return publicFamilies(
    configs.map((entry) => ({ name: entry.id, config: entry.data })),
    reports.map((entry) => entry.data),
    Object.fromEntries(systems.map(({ id, data }) => [id, data.display_name])),
  );
}

/** Returns the systems with a page on a family's board: those its voted rounds placed. */
export function systemPages(family: VotesFamily): Set<string> {
  return new Set(family.rounds.flatMap(({ results }) => results.standings.map((s) => s.system)));
}

export interface BoardLink {
  href: string;
  label: string;
  current: boolean;
}

export interface RoundLink {
  round: string;
  href: string;
}

/**
 * Returns links to a family's closed rounds, newest first, for the page of the round `named`, or
 * for the board's path, which shows the latest. The latest links the board's path except on its
 * own round page, so the round on screen always links the page itself.
 */
export function roundLinks(family: Family, named?: string): RoundLink[] {
  return family.rounds.toReversed().map(({ round }, index) => ({
    round,
    href: index === 0 && round !== named ? boardPath(family) : roundPath(family, round),
  }));
}

/**
 * Returns a function listing the boards of a family's aspect for one of the family's pages at
 * `page`, which the family's own entry links.
 */
function aspectBoards(families: Family[]): (family: Family, page: string) => BoardLink[] {
  const byAspect = Map.groupBy(families, (family) => family.aspect);
  return (family, page) =>
    (byAspect.get(family.aspect) ?? []).map((item): BoardLink => {
      const current = item.name === family.name;
      return { href: current ? page : boardPath(item), label: item.config.display_name, current };
    });
}

export function familyPaths(families: Family[]) {
  const boards = aspectBoards(families);
  return families.map((family) => ({
    params: boardParams(family),
    props: { family, boards: boards(family, boardPath(family)) },
  }));
}

export function roundPaths(families: Family[]) {
  const boards = aspectBoards(families);
  return families.flatMap((family) =>
    family.rounds.map((round) => ({
      params: { ...boardParams(family), round: round.round },
      props: { family, boards: boards(family, roundPath(family, round.round)), round },
    })),
  );
}

export function systemPaths(families: Family[]) {
  const voted = families.filter((family) => family.scorer === 'votes');
  const pages = new Map(voted.map((family) => [family, systemPages(family)]));
  const byAspect = Map.groupBy(voted, (family) => family.aspect);
  return [...pages].flatMap(([family, systems]) =>
    [...systems].map((system) => ({
      params: { ...boardParams(family), system },
      props: {
        family,
        system,
        boards: (byAspect.get(family.aspect) ?? [])
          .filter((item) => pages.get(item)?.has(system))
          .map((item): BoardLink => ({
            href: systemPath(item, system),
            label: item.config.display_name,
            current: item.name === family.name,
          })),
      },
    })),
  );
}
