import { describe, expect, test } from 'vitest';

import type { VotesConfig } from '@/lib/family';
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

describe('gateRule', () => {
  test.each([
    ['both bounds', { min_chars: 40, max_chars: 6000 }, 'outside 40–6,000 characters'],
    ['a lower bound alone', { min_chars: 1200 }, 'shorter than 1,200 characters'],
    ['an upper bound alone', { max_chars: 800 }, 'longer than 800 characters'],
  ])('names %s', (_, gates, length) => {
    expect(gateRule(config({ gates }))).toBe(
      `An output is dropped when it is blank, unfinished, or ${length}.`,
    );
  });

  test('names only blank and unfinished output when the family sets no gates', () => {
    expect(gateRule(config())).toBe('An output is dropped when it is blank or unfinished.');
  });

  test.each([
    ['a language', { language: 'en' }, 'not in English'],
    [
      'a language and its script',
      { language: 'zh', gates: { script: 'hans' } },
      'not in Simplified Chinese',
    ],
    ['a script alone', { gates: { script: 'hans' } }, 'not in Simplified script'],
  ] as const)('names %s last', (_, overrides, written) => {
    expect(gateRule(config(overrides))).toBe(
      `An output is dropped when it is blank, unfinished, or ${written}.`,
    );
  });
});

describe('methodSteps', () => {
  test("keeps a family's specifics in the hints, under the same details as every family", () => {
    const family = config({ render: 'html', gates: { min_chars: 40 } });

    const steps = methodSteps('votes', family);

    expect(steps.map(({ detail }) => detail)).toEqual(
      methodSteps('votes').map(({ detail }) => detail),
    );
    expect(steps.map(({ step, hint }) => `${step}: ${hint?.definition ?? ''}`)).toEqual([
      'html: Every system answers each case with an HTML page.',
      `gates: ${gateRule(family)}`,
      'pairs: One rater picks the better of two outputs on a case without seeing which systems wrote them.',
      'scores: Bradley-Terry scores with 95% intervals. The reference system stays at 1,000.',
    ]);
  });

  test('gives no hints without a config', () => {
    expect(methodSteps('votes').map(({ hint }) => hint)).toEqual([
      undefined,
      undefined,
      undefined,
      undefined,
    ]);
  });
});
