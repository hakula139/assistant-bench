import { ticks } from 'd3-array';

/** Mantissas per decade, from the densest set to the sparsest. */
const MANTISSAS = [[1, 2, 5], [1, 3], [1]];

/**
 * Picks at most `count` ticks for a log axis over `[min, max]`: 1, 2, and 5 per decade where they
 * fit, then fewer per decade, then every few powers of ten as d3-scale's log ticks do. Linear ticks
 * serve a span too short to hold two of those, or a domain starting at zero on a linear axis.
 */
export function logTicks([min, max]: [number, number], count: number): number[] {
  const values = min > 0 && max / min >= 3 ? decadeTicks(min, max, count) : [];
  return values.length >= 2 ? values : linearTicks(min, max, count);
}

function decadeTicks(min: number, max: number, count: number): number[] {
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
      return values;
    }
  }
  const low = Math.ceil(Math.log10(min));
  const high = Math.floor(Math.log10(max));
  for (let hint = Math.min(high - low, count); hint > 0; hint--) {
    const exponents = ticks(low, high, hint);
    if (exponents.length <= count) {
      return exponents.map((exponent) => 10 ** exponent);
    }
  }
  return [];
}

function linearTicks(min: number, max: number, count: number): number[] {
  // d3 treats the count as a hint and may return more, so ask for fewer until they fit.
  for (let hint = count; hint > 1; hint--) {
    const values = ticks(min, max, hint);
    if (values.length <= count) {
      return values;
    }
  }
  return [min, max];
}
