import { describe, expect, test } from 'vitest';

import { formatLengthBias, formatPercentRange, formatScore, formatTau } from '@/lib/format';

describe('formatScore', () => {
  test.each([
    [-161.4, '\u2212161'],
    [1628.2, '1,628'],
  ])('prints %d as a whole score with a minus sign', (score, text) => {
    expect(formatScore(score)).toBe(text);
  });
});

describe('formatTau', () => {
  test.each([
    [-0.25, '\u22120.25'],
    [0.6, '0.60'],
  ])('prints %d with a minus sign where it is negative', (value, text) => {
    expect(formatTau(value)).toBe(text);
  });
});

describe('formatPercentRange', () => {
  test.each([
    [{ lower: 0.0301, upper: 0.5635 }, '3–56%'],
    [{ lower: 0.995, upper: 1 }, '100–100%'],
    [{ lower: 0, upper: 0.39 }, '0–39%'],
    [{ lower: 0.285, upper: 0.5 }, '29–50%'],
  ])('rounds %j to whole percents on both ends', (interval, text) => {
    expect(formatPercentRange(interval)).toBe(text);
  });
});

describe('formatLengthBias', () => {
  test.each([
    [-6.54, 'Favors shorter, 6.5'],
    [3, 'Favors longer, 3.0'],
  ])('names the length a bias of %d favors', (bias, text) => {
    expect(formatLengthBias(bias)).toBe(text);
  });
});
