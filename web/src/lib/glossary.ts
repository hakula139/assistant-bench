import { formatScore } from '@/lib/format';
import { REFERENCE_SCORE } from '@/lib/reference';

/** A term as the boards label it, with what it means. */
export interface Term {
  term: string;
  definition: string;
}

const reference = formatScore(REFERENCE_SCORE);

/** The board terms, shared by the boards' labels, their definitions on tap, and the methodology. */
export const terms = {
  rank: {
    term: 'Rank',
    definition:
      'From 1 plus the systems whose interval lies wholly above this one, to 1 plus those whose interval reaches above its lower end.',
  },
  reference: {
    term: 'Reference',
    definition: `The system every score is relative to, fixed at ${reference}. When a round cannot rank it, the most-voted system stands in.`,
  },
  preliminary: {
    term: 'Preliminary',
    definition:
      'Fewer than 40 decisive votes, those won by either side, so the score may still move.',
  },
  unranked: {
    term: 'Unranked',
    definition:
      'Listed below the table without a score until enough comparisons connect it to the anchor.',
  },
  votes: {
    term: 'Votes',
    definition: "The rater's votes on the system, leaving out results settled without a vote.",
  },
  bothBad: { term: 'Both bad', definition: 'The share of its votes judged both bad.' },
  gateFailures: {
    term: 'Gate failures',
    definition: 'The share of its outputs that failed a gate.',
  },
  costLatency: {
    term: 'Cost / output and p50 latency',
    definition: 'The mean cost at list prices and the median time to the complete response.',
  },
  raterAgreement: {
    term: 'Rater agreement',
    definition:
      'How often a side-swapped repeat matched the original vote. Below 70%, the intervals overstate how well systems separate.',
  },
  separatedPairs: {
    term: 'Separated pairs',
    definition: 'The share of system pairs whose intervals do not overlap.',
  },
  splitHalf: {
    term: 'Split-half τ',
    definition:
      "Kendall's τ between the orders fit to odd and to even sessions, where 1 means the same order.",
  },
  leftOut: {
    term: 'Left out of the fit',
    definition:
      'Votes on a retired case, on an output since removed or now failing a gate, or on a pair the outputs now settle.',
  },
  passRate: {
    term: 'Pass rate',
    definition: 'The share of trials whose tests pass, counting errored trials as failures.',
  },
  testsCostLatency: {
    term: 'Cost / trial and p50 latency',
    definition: "A trial's mean cost at list prices and its median time.",
  },
} satisfies Record<string, Term>;

/** The anchor's definition in a round that cannot rank the reference, naming what it stands in for. */
export function fallbackAnchor(referenceName: string): Term {
  return {
    term: 'Anchor',
    definition: `The most-voted system, fixed at ${reference} because the reference, ${referenceName}, is not ranked yet.`,
  };
}

/** What each gate rejects, in the order `failed_gates` checks them. */
export const gates = {
  empty: { term: 'Empty', definition: 'The output is blank.' },
  truncated: {
    term: 'Truncated',
    definition: 'The model stopped before finishing, as at its output limit.',
  },
  length: { term: 'Length', definition: "The output falls outside the family's character bounds." },
  language: {
    term: 'Language',
    definition: "Most of the prose and code comments are in a language other than the family's.",
  },
  script: {
    term: 'Script',
    definition: 'The output holds Traditional-only characters that the instruction lacks.',
  },
} satisfies Record<string, Term>;
