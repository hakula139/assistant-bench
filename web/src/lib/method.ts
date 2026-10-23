import type { Scorer, VotesConfig } from '@/lib/family';
import { REFERENCE_SCORE } from '@/lib/forest';
import { formatCount, formatScore } from '@/lib/format';
import type { Term } from '@/lib/glossary';

/** One stage of how a scorer turns outputs into a board, as the method strip draws it. */
export interface MethodStep {
  /** The stage, where a run's stage names what it yields: a text output, a page, or a trial. */
  step: 'text' | 'html' | 'trial' | 'gates' | 'pairs' | 'scores' | 'tests' | 'rate';
  title: string;
  detail: string;
  /** One family's specifics, such as its exact gates, which the detail opens on tap. */
  hint?: Term;
}

/** How each scorer describes its boards, so a new scorer adds an entry here. */
export const scorers: Record<Scorer, { title: string; summary: string }> = {
  votes: { title: 'Votes', summary: 'Ranked by blind pairwise votes' },
  tests: { title: 'Tests', summary: 'Scored by automated tests' },
};

const languages: Record<NonNullable<VotesConfig['language']>, string> = {
  en: 'English',
  zh: 'Chinese',
};

const anyOf = new Intl.ListFormat('en', { type: 'disjunction' });

/** What drops a voted output before it reaches the rater, the checks `failed_gates` makes. */
export function gateRule({ language, gates }: VotesConfig): string {
  const { min_chars: min, max_chars: max } = gates;
  const length =
    min !== undefined && max !== undefined
      ? `outside ${formatCount(min)}–${formatCount(max)} characters`
      : min !== undefined
        ? `shorter than ${formatCount(min)} characters`
        : max !== undefined
          ? `longer than ${formatCount(max)} characters`
          : undefined;
  const name = language && languages[language];
  const written = gates.script === 'hans' ? `Simplified ${name ?? 'script'}` : name;
  const problems = ['blank', 'unfinished', length, written && `not in ${written}`];
  return `An output is dropped when it is ${anyOf.format(problems.filter((problem) => problem !== undefined))}.`;
}

const runs: Record<VotesConfig['render'], { step: MethodStep['step']; answer: string }> = {
  text: { step: 'text', answer: 'in text' },
  html: { step: 'html', answer: 'with an HTML page' },
};

/**
 * A scorer's stages in a few words each, alike for every family. Given one family's config, each
 * votes step also carries a hint with that family's specifics.
 */
export function methodSteps(scorer: Scorer, config?: VotesConfig): MethodStep[] {
  switch (scorer) {
    case 'votes': {
      const run = runs[config?.render ?? 'text'];
      return [
        {
          step: run.step,
          title: 'Run',
          detail: 'same cases for all',
          hint: config && {
            term: 'Run',
            definition: `Every system answers each case ${run.answer}.`,
          },
        },
        {
          step: 'gates',
          title: 'Gates',
          detail: 'bad outputs dropped',
          hint: config && { term: 'Gates', definition: gateRule(config) },
        },
        {
          step: 'pairs',
          title: 'Blind pairs',
          detail: 'system names hidden',
          hint: config && {
            term: 'Blind pairs',
            definition:
              'One rater picks the better of two outputs on a case without seeing which systems wrote them.',
          },
        },
        {
          step: 'scores',
          title: 'Scores',
          detail: 'ranked from votes',
          hint: config && {
            term: 'Scores',
            definition: `Bradley-Terry scores with 95% intervals. The reference system stays at ${formatScore(REFERENCE_SCORE)}.`,
          },
        },
      ];
    }
    case 'tests':
      return [
        { step: 'trial', title: 'Run', detail: 'same tasks for all' },
        { step: 'tests', title: 'Tests', detail: "each task's own tests" },
        { step: 'rate', title: 'Pass rate', detail: 'share of tasks passed' },
      ];
  }
}
