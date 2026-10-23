import { describe, expect, test } from 'vitest';

import { formatLengthBias } from '@/lib/format';

describe('formatLengthBias', () => {
  test.each([
    [-6.54, 'Favors shorter, 6.5'],
    [3, 'Favors longer, 3.0'],
  ])('names the length a bias of %d favors', (bias, text) => {
    expect(formatLengthBias(bias)).toBe(text);
  });
});
