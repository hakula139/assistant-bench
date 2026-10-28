import { describe, expect, test } from 'vitest';

import type { FamilyConfig, VotesConfig } from '@/lib/family';
import { boardGuide, boardLabels, gateRule, methodSteps } from '@/lib/method';

const config = (overrides: Partial<Omit<VotesConfig, 'aspect' | 'public'>> = {}): VotesConfig => ({
  scorer: 'votes',
  display_name: 'Arena Writing',
  systems: ['acme-large'],
  reference: 'acme-large',
  render: 'text',
  gates: {},
  public: true,
  ...overrides,
});

const joiner = String.fromCodePoint(0x2060);

const rule = (problems: string) => `An output loses its pairs without a vote if it is ${problems}.`;

describe('gateRule', () => {
  test.each([
    ['both bounds', { min_chars: 40, max_chars: 6000 }, `outside 40–${joiner}6,000`],
    ['a lower bound alone', { min_chars: 1200 }, 'shorter than 1,200'],
    ['an upper bound alone', { max_chars: 800 }, 'longer than 800'],
  ])('names %s with its unit on the same line', (_, gates, length) => {
    expect(gateRule(config({ gates }))).toBe(rule(`blank, cut off, or ${length} characters`));
  });

  test('names only blank and cut-off outputs when the family sets no gates', () => {
    expect(gateRule(config())).toBe(rule('blank or cut off'));
  });

  test.each([
    ['a language', { language: 'en' }, 'or not in English'],
    [
      'a language and the script',
      { language: 'zh', gates: { script: 'hans' } },
      'not in Chinese, or written with Traditional characters',
    ],
    ['the script alone', { gates: { script: 'hans' } }, 'or written with Traditional characters'],
  ] as const)('names %s last, each as its own check', (_, overrides, written) => {
    expect(gateRule(config(overrides))).toBe(rule(`blank, cut off, ${written}`));
  });
});

describe('methodSteps', () => {
  test.each([
    ['text', { render: 'text' }, 'text: Every system answers each case in text.'],
    ['html', { render: 'html' }, 'html: Every system answers each case with an HTML page.'],
    [
      'diffs under an agent and a turn budget',
      { render: 'diff', harness: 'terminus-2', max_turns: 40 },
      "diff: Every system changes each task's repository through the Terminus 2 agent, within 40 turns.",
    ],
    [
      'diffs before it lists systems, and so an agent',
      { render: 'diff', systems: [] },
      "diff: Every system changes each task's repository.",
    ],
  ] satisfies [string, Partial<VotesConfig>, string][])(
    'names the run of a family that renders %s',
    (_, overrides, run) => {
      const [step] = methodSteps('votes', config(overrides));

      expect(`${step.step}: ${step.hint ?? ''}`).toBe(run);
    },
  );

  test("keeps a voted family's specifics in the hints, under the details every board shows", () => {
    const family = config({ gates: { min_chars: 40 } });

    const steps = methodSteps('votes', family);

    expect(steps.map(({ detail }) => detail)).toEqual(
      methodSteps('votes').map(({ detail }) => detail),
    );
    expect(steps.slice(1).map(({ hint }) => hint)).toEqual([
      gateRule(family),
      'One rater votes on two outputs for a case without seeing which systems wrote them.',
      'Elo-like point scores from a Bradley-Terry fit. Each round records its anchor and fitting settings. Uncertainty for new cases and voters is unavailable until the method is calibrated.',
    ]);
  });

  test('gates the solutions of a family that renders diffs on their tests', () => {
    const family = config({ render: 'diff', harness: 'terminus-2', max_turns: 40 });

    const steps = methodSteps('votes', family);

    expect(steps.map(({ title }) => title)).toEqual(['Run', 'Tests', 'Blind pairs', 'Scores']);
    expect(steps.slice(0, 3).map(({ step, detail, hint }) => [step, detail, hint])).toEqual([
      [
        'diff',
        'same tasks for all',
        "Every system changes each task's repository through the Terminus 2 agent, within 40 turns.",
      ],
      [
        'tests',
        'failing solutions lose',
        "A solution that fails the task's hidden tests loses to each passing one without a vote.",
      ],
      [
        'pairs',
        'system names hidden',
        'One rater votes on the diffs of two passing solutions to a task without seeing which systems wrote them.',
      ],
    ]);
  });

  test("gives a tests family's steps their hints", () => {
    const family: FamilyConfig = {
      scorer: 'tests',
      display_name: 'Tasks',
      systems: [],
      public: true,
    };

    const steps = methodSteps('tests', family);

    expect(steps.map(({ title, hint }) => `${title}: ${hint ?? ''}`)).toEqual([
      'Run: Every system attempts each task.',
      "Tests: The task's own tests score each trial.",
      'Pass rate: The share of trials whose tests pass, averaged over tasks so that each task counts once. A trial whose tests never ran fails.',
    ]);
  });

  test.each(['votes', 'tests'] as const)(
    'gives the %s steps no hints without a config',
    (scorer) => {
      expect(methodSteps(scorer).filter(({ hint }) => hint !== undefined)).toEqual([]);
    },
  );
});

describe('boardGuide and boardLabels', () => {
  test.each([
    [
      'cases',
      config(),
      ['Ranked by blind pairwise votes', 'Cases', 'votes'],
      ['Gate failures', 'Length bias', 'output'],
    ],
    [
      'diffs',
      config({ render: 'diff' }),
      ['Ranked by hidden tests, then blind pairwise votes', 'Tasks', 'tests-then-votes'],
      ['Failed tests', 'Diff length bias', 'solution'],
    ],
  ] as const)(
    'describe and label the board of a voted family that renders %s',
    (_, family, guide, labels) => {
      const { summary, inputs, section } = boardGuide(family);
      const { gateFailures, lengthBias, unit } = boardLabels(family);

      expect([summary, inputs, section]).toEqual(guide);
      expect([gateFailures.term, lengthBias.term, unit]).toEqual(labels);
    },
  );
});
