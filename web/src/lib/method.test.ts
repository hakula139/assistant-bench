import { describe, expect, test } from 'vitest';

import type { FamilyConfig, VotesConfig } from '@/lib/family';
import { gateRule, methodSteps } from '@/lib/method';

const config = (overrides: Partial<VotesConfig> = {}): VotesConfig => ({
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
    ['text', 'text: Every system answers each case in text.'],
    ['html', 'html: Every system answers each case with an HTML page.'],
  ] as const)('names the run of a family that renders %s', (render, run) => {
    const [step] = methodSteps('votes', config({ render }));

    expect(`${step.step}: ${step.hint ?? ''}`).toBe(run);
  });

  test("keeps a voted family's specifics in the hints, under the details every board shows", () => {
    const family = config({ gates: { min_chars: 40 } });

    const steps = methodSteps('votes', family);

    expect(steps.map(({ detail }) => detail)).toEqual(
      methodSteps('votes').map(({ detail }) => detail),
    );
    expect(steps.slice(1).map(({ hint }) => hint)).toEqual([
      gateRule(family),
      'One rater votes on two outputs for a case without seeing which systems wrote them.',
      "Elo-like scores from a Bradley-Terry fit, with 95% intervals. The board's anchor, its reference or the most-voted system, is fixed at 1,000.",
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
      'Pass rate: The share of trials whose tests pass, counting errored trials as failures.',
    ]);
  });

  test.each(['votes', 'tests'] as const)(
    'gives the %s steps no hints without a config',
    (scorer) => {
      expect(methodSteps(scorer).filter(({ hint }) => hint !== undefined)).toEqual([]);
    },
  );
});
