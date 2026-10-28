const whole = new Intl.NumberFormat('en', { maximumFractionDigits: 0 });
const percent = new Intl.NumberFormat('en', { style: 'percent', maximumFractionDigits: 0 });

const cost = new Intl.NumberFormat('en', {
  style: 'currency',
  currency: 'USD',
  maximumSignificantDigits: 2,
});

const seconds = new Intl.NumberFormat('en', {
  style: 'unit',
  unit: 'second',
  unitDisplay: 'narrow',
  maximumFractionDigits: 1,
});

const tau = new Intl.NumberFormat('en', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const bias = new Intl.NumberFormat('en', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const penalty = new Intl.NumberFormat('en', { maximumSignificantDigits: 21 });

/** Shown wherever a report holds no value, as for an unpriced route. */
export const MISSING = '–';

/** Swaps the hyphen `Intl` writes for a negative number with a typographic minus sign. */
const minus = (text: string) => text.replace('-', '\u2212');

export const formatScore = (value: number) => minus(whole.format(value));
export const formatTau = (value: number | null) =>
  value === null ? MISSING : minus(tau.format(value));
export const formatCount = (value: number) => whole.format(value);

/** Counts a regular English noun, as in "1 vote" and "34 votes". */
export const formatCountOf = (value: number, noun: string) =>
  `${whole.format(value)} ${value === 1 ? noun : `${noun}s`}`;

/** Prints a recorded fit setting exactly in plain decimal notation, as in "0.0125" for 1.25e-2. */
export const formatPenalty = (value: number) => penalty.format(value);

export const formatPercent = (value: number | null) =>
  value === null ? MISSING : percent.format(value);

/** Prints an interval of shares as whole percents, as in "52–99%". */
export const formatPercentRange = ({ lower, upper }: { lower: number; upper: number }) => {
  const from = percent
    .formatToParts(lower)
    .filter((part) => part.type !== 'percentSign')
    .map((part) => part.value)
    .join('');
  return `${from}–${percent.format(upper)}`;
};

export const formatCost = (value: number | null) => (value === null ? MISSING : cost.format(value));
export const formatSeconds = (value: number | null) =>
  value === null ? MISSING : seconds.format(value);

/** Names the output length the rater favors and by how many points, as in "Favors shorter, 6.5". */
export const formatLengthBias = (value: number) =>
  `Favors ${value >= 0 ? 'longer' : 'shorter'}, ${bias.format(Math.abs(value))}`;
