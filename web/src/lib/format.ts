import type { Decision } from '@/lib/report';

const score = new Intl.NumberFormat('en', { maximumFractionDigits: 0 });
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
const count = new Intl.NumberFormat('en', { maximumFractionDigits: 0 });

/** Shown wherever a report holds no value, as for an unpriced route. */
export const MISSING = '–';

export const formatScore = (value: number) => score.format(value);
export const formatTau = (value: number | null) => (value === null ? MISSING : tau.format(value));
export const formatCount = (value: number) => count.format(value);
export const formatPercent = (value: number | null) =>
  value === null ? MISSING : percent.format(value);
export const formatCost = (value: number | null) => (value === null ? MISSING : cost.format(value));
export const formatSeconds = (value: number | null) =>
  value === null ? MISSING : seconds.format(value);

export const formatInterval = ({ lower, upper }: { lower: number; upper: number }) =>
  `${formatScore(lower)}–${formatScore(upper)}`;

const tieBreaks: Record<Decision['tie_break'], string> = {
  score: 'the higher score',
  cost: 'the lower cost',
  latency: 'the lower latency',
};

/** Says how a leader that shares rank 1 was chosen, or null when it alone ranks first. */
export const formatTieBreak = ({ separated, tie_break }: Decision) =>
  separated ? null : `tied at rank 1, chosen by ${tieBreaks[tie_break]}`;

export const formatRank = (best: number, worst: number) =>
  best === worst ? `${best}` : `${best}–${worst}`;
