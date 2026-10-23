import type { FamilyConfig, Scorer, VotesConfig } from '@/lib/family';
import { formatCount, formatScore } from '@/lib/format';
import { terms } from '@/lib/glossary';
import { REFERENCE_SCORE } from '@/lib/reference';

/** One stage of how a scorer turns outputs into a board, as the method strip draws it. */
export interface MethodStep {
  /** The stage, where a run's stage names what it yields: a text output, a page, or a trial. */
  step: 'text' | 'html' | 'trial' | 'gates' | 'pairs' | 'scores' | 'tests' | 'rate';
  title: string;
  detail: string;
  /** What the step means on a family's board, which its detail opens. */
  hint?: string;
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

/** Keeps a range's two bounds and its unit on one line, where a break may follow an en dash. */
const joiner = String.fromCodePoint(0x2060);
/** A no-break space keeps each bound on the line of its unit. */
const characters = ' characters';

/** What makes a voted output lose its pairs without a vote, the checks `failed_gates` makes. */
export function gateRule({ language, gates }: VotesConfig): string {
  const { min_chars: min, max_chars: max } = gates;
  const length =
    min !== undefined && max !== undefined
      ? `outside ${formatCount(min)}–${joiner}${formatCount(max)}${characters}`
      : min !== undefined
        ? `shorter than ${formatCount(min)}${characters}`
        : max !== undefined
          ? `longer than ${formatCount(max)}${characters}`
          : undefined;
  const problems = [
    'blank',
    'cut off',
    length,
    language && `not in ${languages[language]}`,
    gates.script === 'hans' ? 'written with Traditional characters' : undefined,
  ].filter((problem) => problem !== undefined);
  return `An output loses its pairs without a vote if it is ${anyOf.format(problems)}.`;
}

const runs: Record<VotesConfig['render'], { step: MethodStep['step']; answer: string }> = {
  text: { step: 'text', answer: 'in text' },
  html: { step: 'html', answer: 'with an HTML page' },
};

/**
 * A scorer's stages in a few words each, alike on every board. Given a family's config, each step
 * also carries a hint saying what it means on that family's board.
 */
export function methodSteps(scorer: Scorer, config?: FamilyConfig): MethodStep[] {
  switch (scorer) {
    case 'votes': {
      const votes = config?.scorer === 'votes' ? config : undefined;
      const run = runs[votes?.render ?? 'text'];
      return [
        {
          step: run.step,
          title: 'Run',
          detail: 'same cases for all',
          hint: votes && `Every system answers each case ${run.answer}.`,
        },
        {
          step: 'gates',
          title: 'Gates',
          detail: 'failing outputs lose',
          hint: votes && gateRule(votes),
        },
        {
          step: 'pairs',
          title: 'Blind pairs',
          detail: 'system names hidden',
          hint:
            votes &&
            'One rater votes on two outputs for a case without seeing which systems wrote them.',
        },
        {
          step: 'scores',
          title: 'Scores',
          detail: 'ranked from votes',
          hint:
            votes &&
            `Elo-like scores from a Bradley-Terry fit, with 95% intervals. The reference system is fixed at ${formatScore(REFERENCE_SCORE)}.`,
        },
      ];
    }
    case 'tests': {
      const tests = config?.scorer === 'tests' ? config : undefined;
      return [
        {
          step: 'trial',
          title: 'Run',
          detail: 'same tasks for all',
          hint: tests && 'Every system attempts each task.',
        },
        {
          step: 'tests',
          title: 'Tests',
          detail: "each task's own tests",
          hint: tests && "The task's own tests score each trial.",
        },
        {
          step: 'rate',
          title: 'Pass rate',
          detail: 'share of trials passed',
          hint: tests && terms.passRate.definition,
        },
      ];
    }
  }
}
