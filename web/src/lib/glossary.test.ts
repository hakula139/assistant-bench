import { describe, expect, test } from 'vitest';

import { leaderNote } from '@/lib/glossary';

describe('leaderNote', () => {
  test.each([
    [
      'why a round has no leader',
      null,
      {
        term: 'No leader yet',
        definition: 'None until every system tied for the top has 40 votes with a winner.',
      },
    ],
    [
      'nothing for a leader no system ties',
      { leader: 'acme-pro', separated: true, tie_break: 'score' as const },
      null,
    ],
    [
      'the tie break that chose a tied leader',
      { leader: 'acme-pro', separated: false, tie_break: 'latency' as const },
      { term: 'Tied', definition: 'Tied with the top score, and chosen by the lower latency.' },
    ],
  ])('gives %s', (_, decision, note) => {
    expect(leaderNote(decision)).toEqual(note);
  });
});
