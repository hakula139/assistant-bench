import { CI_PAGES_URL, CI_PROJECT_URL } from 'astro:env/client';

export const SITE_NAME = 'assistant-bench';
export const SITE_DESCRIPTION =
  'Leaderboards for AI models and agents, ranked through blind pairwise voting or automated tests.';

export const REPOSITORY = CI_PROJECT_URL ?? 'https://github.com/hakula139/assistant-bench';
/** The prefix of tree and blob links, which GitLab nests under `/-/`. */
export const SOURCE = CI_PROJECT_URL ? `${CI_PROJECT_URL}/-` : REPOSITORY;

/** Pages deployments are static, so they carry no vote page. */
export const VOTING = CI_PAGES_URL === undefined;

export function sitePath(path: `/${string}`): string {
  return `${import.meta.env.BASE_URL.replace(/\/$/, '')}${path}`;
}

export type HeaderSection = 'leaderboards' | 'methodology' | 'vote';
