import { describe, expect, test } from 'vitest';

import { isPreliminary } from '@/lib/ranking';
import type { VotesRules } from '@/lib/report';

describe('isPreliminary', () => {
  test.each([
    [11, 12, true],
    [12, 12, false],
    [12, 40, true],
    [40, 40, false],
  ])(
    'derives %i decisive votes against the recorded threshold %i',
    (decisive, threshold, expected) => {
      const rules: VotesRules = {
        preliminary_decisive_votes: threshold,
        preliminary_decisive_vote_unit: 'raw-votes',
        rater_agreement_floor: 0.7,
      };

      const preliminary = isPreliminary({ decisive_votes: decisive }, rules);

      expect(preliminary).toBe(expected);
    },
  );
});
