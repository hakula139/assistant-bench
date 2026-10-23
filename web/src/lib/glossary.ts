import { formatPercent, formatScore } from '@/lib/format';
import { AGREEMENT_FLOOR, ANCHOR_SCORE, PRELIMINARY_VOTES } from '@/lib/ranking';
import type { Decision, Defect } from '@/lib/report';

/** A term as the boards label it, with what it means. */
export interface Term {
  term: string;
  definition: string;
}

const score = formatScore(ANCHOR_SCORE);
const floor = formatPercent(AGREEMENT_FLOOR);

/** The board terms, shared by the boards' labels, their definitions on tap, and the methodology. */
export const terms = {
  rank: {
    term: 'Rank',
    definition:
      'The range of ranks the intervals allow: 1 plus the systems clearly above this one, up to 1 plus every system that might be above it.',
  },
  anchor: {
    term: 'Anchor',
    definition: `The system every score is relative to, fixed at ${score}: the family's reference, or the most-voted system when a round cannot rank it.`,
  },
  preliminary: {
    term: 'Preliminary',
    definition: `Fewer than ${PRELIMINARY_VOTES} votes with a winner, so the score may still move.`,
  },
  decisive: {
    term: 'Decisive',
    definition: 'Votes with a winner, leaving out ties and both-bad votes.',
  },
  unranked: {
    term: 'Unranked',
    definition: 'Listed below the table, unscored, until comparisons connect it to the anchor.',
  },
  votes: {
    term: 'Votes',
    definition: "The rater's votes on the system, leaving out pairs settled without a vote.",
  },
  bothBad: { term: 'Both bad', definition: 'The share of its votes judged both bad.' },
  gateFailures: {
    term: 'Gate failures',
    definition:
      'The share of its outputs that failed an automatic check, such as being blank or in the wrong language, and so lost their pairs without a vote.',
  },
  costLatency: {
    term: 'Cost / output and p50 latency',
    definition: 'The mean cost at list prices and the median time to the complete response.',
  },
  leader: {
    term: 'Leader',
    definition: `The top system, once every system tied with it has ${PRELIMINARY_VOTES} votes with a winner. A tie goes to the lower cost, then the lower latency, then the higher score.`,
  },
  raterAgreement: {
    term: 'Rater agreement',
    definition: `How often a side-swapped repeat matched the original vote. Below ${floor}, the intervals overstate how well systems separate.`,
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
  lengthBias: {
    term: 'Length bias',
    definition:
      "How far the rater's votes lean toward the longer or the shorter output, in score points per standard deviation of the pair's relative length difference.",
  },
  passRate: {
    term: 'Pass rate',
    definition: 'The share of trials whose tests pass, counting errored trials as failures.',
  },
  errored: {
    term: 'Errored',
    definition: 'Trials that ended in an error, which count as failures.',
  },
  testsCostLatency: {
    term: 'Cost / trial and p50 latency',
    definition: 'The mean cost per trial at list prices and the median trial time.',
  },
} satisfies Record<string, Term>;

/** The defects the rater can tag an output with, as the vote page and the system page name them. */
export const defectTerms: Record<Defect, Term> = {
  facts: {
    term: 'Wrong facts',
    definition: "Gets a fact wrong, or adds one the prompt doesn't give.",
  },
  punctuation: {
    term: 'Punctuation',
    definition: "Marks that don't suit the language or the style.",
  },
  tone: {
    term: 'Wrong tone',
    definition: 'Too formal, too casual, or wrong for its reader.',
  },
  formulaic: {
    term: 'Formulaic',
    definition: 'Templated, boilerplate prose, such as Chinese 公文腔.',
  },
  length: {
    term: 'Too long',
    definition: 'Says more than its reader needs.',
  },
};

/** The anchor's definition in a round that cannot rank the reference, naming both systems. */
export function fallbackAnchor(anchorName: string, referenceName: string): Term {
  return {
    ...terms.anchor,
    definition: `The most-voted system, ${anchorName}, fixed at ${score} in place of the reference ${referenceName}, which that round could not rank.`,
  };
}

/** What a round's `fingerprint` for a system identifies, quoting the hash. */
export function fingerprintTerm(fingerprint: string): Term {
  return {
    term: 'Fingerprint',
    definition: `${fingerprint}, a hash of the route, effort, harness, and prompt the system ran with that round.`,
  };
}

const tieBreaks: Record<Decision['tie_break'], string> = {
  score: 'the higher score',
  cost: 'the lower cost',
  latency: 'the lower latency',
};

/**
 * Qualifies a round's leader: why the round has none, how it broke a tie with the top score, or
 * null when no other ranked system ties with the leader.
 */
export function leaderNote(decision: Decision | null): Term | null {
  if (decision === null) {
    return {
      term: 'No leader yet',
      definition: `None until every system tied for the top has ${PRELIMINARY_VOTES} votes with a winner.`,
    };
  }
  if (decision.separated) {
    return null;
  }
  return {
    term: 'Tied',
    definition: `Tied with the top score, and chosen by ${tieBreaks[decision.tie_break]}.`,
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
    definition: "The output uses Traditional-only characters that the prompt doesn't.",
  },
} satisfies Record<string, Term>;
