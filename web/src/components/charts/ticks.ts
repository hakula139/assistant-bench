import { ticks } from 'd3-array';

/** Mantissas per decade, from the densest set to the sparsest. */
const MANTISSAS = [[1, 2, 5], [1, 3], [1]];

/**
 * Picks at most `count` ticks for a log axis over `[min, max]`: 1, 2, and 5 per decade where they
 * fit, thinning to fewer per decade, and linear ticks when the span is too short for two of them.
 */
export function logTicks([min, max]: [number, number], count: number): number[] {
  if (max / min >= 3) {
    const first = Math.floor(Math.log10(min));
    const last = Math.ceil(Math.log10(max));
    for (const mantissas of MANTISSAS) {
      const values = [];
      for (let exponent = first; exponent <= last; exponent++) {
        for (const mantissa of mantissas) {
          const value = mantissa * 10 ** exponent;
          if (value >= min && value <= max) {
            values.push(value);
          }
        }
      }
      if (values.length <= count) {
        if (values.length >= 2) {
          return values;
        }
        break;
      }
    }
  }
  // d3 treats the count as a hint and may return more, so ask for fewer until they fit.
  for (let hint = count; hint > 1; hint--) {
    const values = ticks(min, max, hint);
    if (values.length <= count) {
      return values;
    }
  }
  return [min, max];
}
