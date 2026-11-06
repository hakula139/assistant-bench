export const SITE_NAME = 'assistant-bench';
export const SITE_DESCRIPTION =
  'Leaderboards for AI models and agents, ranked through blind pairwise voting or automated tests.';

const project = process.env.CI_PROJECT_URL;

export const REPOSITORY = project ?? 'https://github.com/hakula139/assistant-bench';
/** The prefix of tree and blob links, which GitLab nests under `/-/`. */
export const SOURCE = project ? `${project}/-` : REPOSITORY;

export function sitePath(path: `/${string}`): string {
  return `${import.meta.env.BASE_URL.replace(/\/$/, '')}${path}`;
}

export type HeaderSection = 'leaderboards' | 'methodology' | 'vote';
