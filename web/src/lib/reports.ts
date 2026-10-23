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
 * A family's path segment under its aspect: its short name in kebab case, with accents dropped.
 *
 * @throws Error when the short name has no ASCII letter or digit to build the segment from.
 */
export function boardSlug({ display_name: name }: FamilyConfig): string {
  const slug = name
    .normalize('NFKD')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '');
  if (!slug) {
    throw new Error(`the short name ${name} has no ASCII letter or digit for its board path`);
  }
  return slug;
}

function boardParams(family: Family): { aspect: Aspect; board: string } {
  return { aspect: family.aspect, board: boardSlug(family.config) };
}

/** A family's board path, which every link to the board and its system pages starts with. */
export function boardPath(family: Family): string {
  const { aspect, board } = boardParams(family);
  return `/${aspect}/${board}/`;
}

/** A system's page path on a family's board. */
export function systemPath(family: Family, system: string): string {
  return `${boardPath(family)}${system}/`;
}

/** The site's top-level path segments, which a family ID may not take. */
const reservedNames = new Set<string>([...aspectIds, 'methodology', 'vote']);

/**
 * Pairs each public family's config with its reports, by name, and lists them by name. Families
 * without `public = true` stay off the site together with their reports.
 *
 * @throws Error when two families, listed or not, share a full name, when a family ID names an
 *   aspect or a top-level page, when a public family has no aspect, or when two public families
 *   share a board path.
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
      throw new Error(`families ${owner} and ${name} share the full name ${title}`);
    }
    owners.set(title, name);
    if (reservedNames.has(name)) {
      throw new Error(`family ${name} takes the name of a top-level path`);
    }
  }
  const rounds = Map.groupBy(reports, (report) => report.family);
  const families = configs
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
    .toSorted((a, b) => a.name.localeCompare(b.name));
  const holders = new Map<string, string>();
  for (const family of families) {
    const path = boardPath(family);
    const holder = holders.get(path);
    if (holder !== undefined) {
      throw new Error(`families ${holder} and ${family.name} share the board path ${path}`);
    }
    holders.set(path, family.name);
  }
  return families;
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
  // A `filter` on the aspect fails `no-unnecessary-condition` while the registry has one aspect.
  return Map.groupBy(families, (item) => item.aspect).get(family.aspect) ?? [];
}

/** Returns each family page's params and props: the family and its aspect's boards. */
export function familyPaths(families: Family[]) {
  return families.map((family) => ({
    params: boardParams(family),
    props: {
      family,
      boards: siblings(families, family).map((item): BoardLink => ({
        href: boardPath(item),
        label: item.config.display_name,
        current: item.name === family.name,
      })),
    },
  }));
}

/**
 * Returns each system page's params and props: the family, the system, and the system's pages on
 * the boards of the family's aspect that give it one.
 */
export function systemPaths(families: Family[]) {
  const pages = new Map(families.map((family) => [family.name, systemPages(family)]));
  return families.flatMap((family) =>
    [...systemPages(family)].map((system) => ({
      params: { ...boardParams(family), system },
      props: {
        family,
        system,
        boards: siblings(families, family)
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
