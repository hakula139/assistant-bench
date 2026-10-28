import type { FamilyConfig, Scorer, VotesConfig } from '@/lib/family';
import { formatCount, formatCountOf } from '@/lib/format';
import { terms, type Term } from '@/lib/glossary';
import type { VotesMethod, VotesRules } from '@/lib/report';

export function roundMethod(method: VotesMethod, rules: VotesRules): string {
  return `Bradley-Terry, L2 penalty ${method.l2_penalty}, ${method.points_per_logit} points per logit, anchor ${method.anchor_score}. Each voter contributes at most ${method.actor_cap} votes of weight per family, shared equally across their votes. Settled output pairs share each case's weight. Preliminary coverage requires ${rules.preliminary_decisive_votes} raw decisive votes.`;
}

/** One stage of how a scorer turns outputs into a board, as the method strip draws it. */
export interface MethodStep {
  /** The stage, where a run's stage names what it yields: text, a page, a diff, or a trial. */
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

/** The methodology sections on a render's own protocol, each linked from that render's boards. */
export type RenderSection = 'tests-then-votes';

/** What a voted family's board calls its outputs and their figures. */
export interface BoardLabels {
  /** One output, as cost labels such as "Cost / output" name it. */
  unit: 'output' | 'solution';
  gateFailures: Term;
  lengthBias: Term;
  /** The system page's section on output tokens and latency, and the note under its title. */
  usage: { title: string; note: string };
}

/** How a voted family's board differs by what its `render` says the systems produce. */
interface RenderView {
  run: RuledStep;
  gate: RuledStep;
  /** What one blind pair shows the rater. */
  pair: string;
  /** What the render changes in its scorer's board guide. */
  guide: Partial<BoardGuide> & { section?: RenderSection };
  labels: BoardLabels;
}

const range = "Each system's median and 10th to 90th percentile range";

/** A case family's run, which yields a `step` of text or a page. */
const caseRun = (step: 'text' | 'html', answer: string): RuledStep => ({
  step,
  title: 'Run',
  detail: 'same cases for all',
  rule: () => `Every system answers each case ${answer}.`,
});

const cases: Omit<RenderView, 'run'> = {
  gate: { step: 'gates', title: 'Gates', detail: 'failing outputs lose', rule: gateRule },
  pair: 'two outputs for a case',
  guide: {},
  labels: {
    unit: 'output',
    gateFailures: terms.gateFailures,
    lengthBias: terms.lengthBias,
    usage: { title: 'Output length and latency', note: `${range}.` },
  },
};

const renders: Record<VotesConfig['render'], RenderView> = {
  text: { run: caseRun('text', 'in text'), ...cases },
  html: { run: caseRun('html', 'with an HTML page'), ...cases },
  diff: {
    run: { step: 'diff', title: 'Run', detail: 'same tasks for all', rule: agentRule },
    gate: {
      step: 'tests',
      title: 'Tests',
      detail: 'failing solutions lose',
      rule: () =>
        "A solution that fails the task's hidden tests loses to each passing one without a vote.",
    },
    pair: 'the diffs of two passing solutions to a task',
    guide: {
      summary: 'Ranked by hidden tests, then blind pairwise votes',
      inputs: 'Tasks',
      section: 'tests-then-votes',
    },
    labels: {
      unit: 'solution',
      gateFailures: terms.failedTests,
      lengthBias: terms.diffLengthBias,
      usage: {
        title: 'Output tokens and latency',
        note: `${range}, where latency spans the agent's whole run.`,
      },
    },
  },
};

/**
 * A scorer's stages in a few words each, alike on every board of one scorer and render. Given a
 * family's config, each step also carries a hint saying what it means on that family's board.
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
            'Elo-like point scores from a Bradley-Terry fit. Each round records its anchor and fitting settings. Uncertainty for new cases and voters is unavailable until the method is calibrated.',
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

/** How a family's board describes itself and where it points its readers. */
export interface BoardGuide {
  summary: string;
  /** What its systems answer, as the link to them names it. */
  inputs: string;
  /** The methodology section that explains the board. */
  section: Scorer | RenderSection;
}

/** A family's board guide: its scorer's, with what its render changes. */
export function boardGuide(config: FamilyConfig): BoardGuide {
  const { summary, inputs } = scorers[config.scorer];
  const guide = config.scorer === 'votes' ? renders[config.render].guide : {};
  return { summary, inputs, section: config.scorer, ...guide };
}

/** What a voted family's board calls its outputs and their figures. */
export function boardLabels({ render }: Pick<VotesConfig, 'render'>): BoardLabels {
  return renders[render].labels;
}
