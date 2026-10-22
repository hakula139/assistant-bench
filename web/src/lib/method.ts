import type { Scorer, VotesConfig } from '@/lib/family';
import { REFERENCE_SCORE } from '@/lib/forest';
import { formatCount, formatScore } from '@/lib/format';

/** One stage of how a scorer turns outputs into a board, as the method strip draws it. */
export interface MethodStep {
  /** The stage, where a run's stage names what it yields: a text output, a page, or a trial. */
  step: 'text' | 'html' | 'trial' | 'gates' | 'pairs' | 'scores' | 'tests' | 'rate';
  title: string;
  detail: string;
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

/** What a voted output must pass to reach the rater, in the order `failed_gates` checks it. */
export function gateLabels({ language, gates }: VotesConfig): string[] {
  const { min_chars: min, max_chars: max } = gates;
  const length =
    min !== undefined && max !== undefined
      ? `${formatCount(min)}–${formatCount(max)} characters`
      : min !== undefined
        ? `at least ${formatCount(min)} characters`
        : max !== undefined
          ? `at most ${formatCount(max)} characters`
          : undefined;
  return [
    'finished, not blank',
    ...(length ? [length] : []),
    ...(language ? [`in ${languages[language]}`] : []),
    ...(gates.script === 'hans' ? ['Simplified script'] : []),
  ];
}

const runs: Record<VotesConfig['render'], MethodStep> = {
  text: { step: 'text', title: 'Run', detail: 'a text output per case' },
  html: { step: 'html', title: 'Run', detail: 'an HTML page per case' },
};

/** A scorer's stages, filled in with one family's render and gates when it is given. */
export function methodSteps(scorer: Scorer, config?: VotesConfig): MethodStep[] {
  switch (scorer) {
    case 'votes':
      return [
        config ? runs[config.render] : { ...runs.text, detail: 'an output per case' },
        {
          step: 'gates',
          title: 'Gates',
          detail: config ? gateLabels(config).join(', ') : 'checked before any vote',
        },
        { step: 'pairs', title: 'Blind pairs', detail: 'one rater' },
        {
          step: 'scores',
          title: 'Scores',
          detail: `Bradley-Terry, reference at ${formatScore(REFERENCE_SCORE)}, 95% intervals`,
        },
      ];
    case 'tests':
      return [
        { step: 'trial', title: 'Run', detail: 'one trial per task' },
        { step: 'tests', title: 'Tests', detail: "the task's own tests" },
        { step: 'rate', title: 'Pass rate', detail: 'errored trials fail' },
      ];
  }
}
