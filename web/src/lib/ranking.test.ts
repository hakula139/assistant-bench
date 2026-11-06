import { describe, expect, test } from 'vitest';

import { isPreliminary } from '@/lib/ranking';
import type { ScoreInterval, VotesRules } from '@/lib/report';

describe('isPreliminary', () => {
  test.each<[ScoreInterval | null, number, boolean]>([
    [{ lower: 940, upper: 1060 }, 50, true],
    [{ lower: 950, upper: 1050 }, 50, false],
    [{ lower: 950, upper: 1050 }, 40, true],
    [null, 50, true],
  ])(
    'compares the half-width of %j with the recorded threshold %i',
    (interval, threshold, expected) => {
      const rules: VotesRules = { preliminary_half_width: threshold };

      const preliminary = isPreliminary(interval, rules);

      expect(preliminary).toBe(expected);
    },
  );
});
