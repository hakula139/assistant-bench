import type { FamilyConfig, Scorer, VotesConfig } from '@/lib/family';
import { formatCount, formatCountOf, formatScore } from '@/lib/format';
import { terms } from '@/lib/glossary';
import { ANCHOR_SCORE } from '@/lib/ranking';

/** One stage of how a scorer turns outputs into a board, as the method strip draws it. */
export interface MethodStep {
  /** The stage, where a run's stage names what it yields: a text output, a page, a diff, or a trial. */
  step: 'text' | 'html' | 'diff' | 'trial' | 'gates' | 'pairs' | 'scores' | 'tests' | 'rate';
  title: string;
  detail: string;
  /** What the step means on a family's board, which its detail opens. */
  hint?: string;
}

/**
 * How each scorer describes its boards, with `inputs` naming what its systems answer, so a new
 * scorer adds an entry here.
 */
export const scorers: Record<Scorer, { title: string; summary: string; inputs: string }> = {
  votes: { title: 'Votes', summary: 'Ranked by blind pairwise votes', inputs: 'Cases' },
  tests: { title: 'Tests', summary: 'Scored by automated tests', inputs: 'Tasks' },
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

const harnesses: Record<NonNullable<FamilyConfig['harness']>, string> = {
  'terminus-2': 'Terminus 2',
};

/** How a task family's systems change each repository: through which agent, in how many turns. */
function agentRule({ harness, max_turns: turns }: VotesConfig): string {
  const agent = harness === undefined ? '' : ` through the ${harnesses[harness]} agent`;
  const budget = turns === undefined ? '' : `, within ${formatCountOf(turns, 'turn')}`;
  return `Every system changes each task's repository${agent}${budget}.`;
}

/** A step whose hint `rule` writes from the family's config. */
type RuledStep = Omit<MethodStep, 'hint'> & { rule: (config: VotesConfig) => string };

const caseRun = { title: 'Run', detail: 'same cases for all' };
const caseGate: RuledStep = {
  step: 'gates',
  title: 'Gates',
  detail: 'failing outputs lose',
  rule: gateRule,
};

/** The steps of a voted family that differ by what its `render` says the systems produce. */
const renders: Record<VotesConfig['render'], { run: RuledStep; gate: RuledStep; pair: string }> = {
  text: {
    run: { step: 'text', ...caseRun, rule: () => 'Every system answers each case in text.' },
    gate: caseGate,
    pair: 'two outputs for a case',
  },
  html: {
    run: {
      step: 'html',
      ...caseRun,
      rule: () => 'Every system answers each case with an HTML page.',
    },
    gate: caseGate,
    pair: 'two outputs for a case',
  },
  diff: {
    run: { step: 'diff', title: 'Run', detail: 'same tasks for all', rule: agentRule },
    gate: {
      step: 'tests',
      title: 'Tests',
      detail: 'failing solutions lose',
      rule: () =>
        "A solution that fails the task's hidden tests loses its pairs without a vote, and two failing solutions tie.",
    },
    pair: 'the diffs of two passing solutions to a task',
  },
};

/**
 * A scorer's stages in a few words each, alike on every board of the scorer whose systems produce
 * the same kind of output. Given a family's config, each step also carries a hint saying what it
 * means on that family's board.
 */
export function methodSteps(scorer: Scorer, config?: FamilyConfig): MethodStep[] {
  switch (scorer) {
    case 'votes': {
      const votes = config?.scorer === 'votes' ? config : undefined;
      const { run, gate, pair } = renders[votes?.render ?? 'text'];
      const ruled = ({ rule, ...step }: RuledStep): MethodStep => ({
        ...step,
        hint: votes && rule(votes),
      });
      return [
        ruled(run),
        ruled(gate),
        {
          step: 'pairs',
          title: 'Blind pairs',
          detail: 'system names hidden',
          hint: votes && `One rater votes on ${pair} without seeing which systems wrote them.`,
        },
        {
          step: 'scores',
          title: 'Scores',
          detail: 'ranked from votes',
          hint:
            votes &&
            `Elo-like scores from a Bradley-Terry fit, with 95% intervals. The board's anchor, its reference or the most-voted system, is fixed at ${formatScore(ANCHOR_SCORE)}.`,
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

const rendersDiffs = (config: FamilyConfig) =>
  config.scorer === 'votes' && config.render === 'diff';

/**
 * Where a family's board points its readers: the methodology section that explains the board, and
 * the name of what its systems answer.
 */
export function boardGuide(config: FamilyConfig): { section: string; inputs: string } {
  return rendersDiffs(config)
    ? { section: 'coding', inputs: 'Tasks' }
    : { section: config.scorer, inputs: scorers[config.scorer].inputs };
}

/** The leaderboard's gate-failure column, which a family that renders diffs names for its tests. */
export const gateFailuresTerm = (config: FamilyConfig) =>
  rendersDiffs(config) ? terms.failedTests : terms.gateFailures;
