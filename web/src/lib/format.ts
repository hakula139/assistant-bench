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

/** Shown wherever a report holds no value, as for an unpriced route. */
export const MISSING = '–';

export const formatScore = (value: number) => whole.format(value);
export const formatTau = (value: number | null) => (value === null ? MISSING : tau.format(value));
export const formatCount = (value: number) => whole.format(value);

/** Counts a regular English noun, as in "1 vote" and "34 votes". */
export const formatCountOf = (value: number, noun: string) =>
  `${whole.format(value)} ${value === 1 ? noun : `${noun}s`}`;

export const formatPercent = (value: number | null) =>
  value === null ? MISSING : percent.format(value);

export const formatCost = (value: number | null) => (value === null ? MISSING : cost.format(value));
export const formatSeconds = (value: number | null) =>
  value === null ? MISSING : seconds.format(value);

/** Names the output length the rater favors and by how many points, as in "Favors shorter, 6.5". */
export const formatLengthBias = (value: number) =>
  `Favors ${value >= 0 ? 'longer' : 'shorter'}, ${bias.format(Math.abs(value))}`;
