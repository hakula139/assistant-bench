import { describe, expect, test } from 'vitest';

import type { VotesConfig } from '@/lib/family';
import { gateLabels, methodSteps } from '@/lib/method';

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

describe('gateLabels', () => {
  test.each([
    ['both bounds', { min_chars: 40, max_chars: 6000 }, '40–6,000 characters'],
    ['a lower bound alone', { min_chars: 1200 }, 'at least 1,200 characters'],
    ['an upper bound alone', { max_chars: 800 }, 'at most 800 characters'],
  ])('names %s', (_, gates, length) => {
    expect(gateLabels(config({ gates }))).toEqual(['finished, not blank', length]);
  });

  test('names only the gates every family runs when the family sets none', () => {
    expect(gateLabels(config())).toEqual(['finished, not blank']);
  });

  test('names the language and the script after the length', () => {
    const labels = gateLabels(
      config({ language: 'zh', gates: { max_chars: 800, script: 'hans' } }),
    );

    expect(labels).toEqual([
      'finished, not blank',
      'at most 800 characters',
      'in Chinese',
      'Simplified script',
    ]);
  });
});

describe('methodSteps', () => {
  test("fills a voted family's run and gates in from its config", () => {
    const steps = methodSteps('votes', config({ render: 'html', gates: { min_chars: 40 } }));

    expect(steps.map(({ title, detail }) => `${title}: ${detail}`)).toEqual([
      'Run: an HTML page per case',
      'Gates: finished, not blank, at least 40 characters',
      'Blind pairs: one rater',
      'Scores: Bradley-Terry, reference at 1,000, 95% intervals',
    ]);
  });

  test('describes the votes steps in general without a config', () => {
    const [run, gates] = methodSteps('votes');

    expect(run.detail).toBe('an output per case');
    expect(gates.detail).toBe('checked before any vote');
  });
});
