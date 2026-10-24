import { formatPercent, formatScore } from '@/lib/format';
import type { Defect, VotesMethod, VotesRules } from '@/lib/report';

/** A term as the boards label it, with what it means. */
export interface Term {
  term: string;
  definition: string;
}

export const inferenceNote =
  'Uncertainty for new cases and voters is unavailable until the method is calibrated.';

/** The board terms, shared by the boards' labels, their definitions on tap, and the methodology. */
export const terms = {
  rank: {
    term: 'Rank',
    definition: 'The order of fitted point scores within this round. Equal scores share a rank.',
  },
  anchor: {
    term: 'Anchor',
    definition:
      "The system every score is relative to: the family's reference, or the most-voted system when a round cannot rank it.",
  },
  preliminary: {
    term: 'Preliminary',
    definition: "A coverage marker for systems below the round's recorded decisive-vote threshold.",
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
  failedTests: {
    term: 'Failed tests',
    definition:
      "The share of its solutions that failed the task's hidden tests, and so lost their pairs without a vote.",
  },
  costLatency: {
    term: 'Cost / output and p50 latency',
    definition: 'The mean cost at list prices and the median time to the complete response.',
  },
  solutionCostLatency: {
    term: 'Cost / solution and p50 latency',
    definition:
      "The mean cost of a solution at list prices and the median time of the agent's whole run on a task.",
  },
  raterAgreement: {
    term: 'Rater agreement',
    definition:
      'How often a side-swapped repeat matched the original vote. This describes repeat consistency.',
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
  diffLengthBias: {
    term: 'Diff length bias',
    definition:
      "How far the rater's votes lean toward the longer or the shorter diff, in score points per standard deviation of the pair's relative length difference.",
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
  scope: {
    term: 'Off-scope',
    definition: "Changes what the request didn't ask for, or leaves stray files.",
  },
  conventions: {
    term: 'Conventions',
    definition: "Ignores the repository's style, layout, or AGENTS.md.",
  },
  complexity: {
    term: 'Overbuilt',
    definition: 'More code, abstraction, or configuration than the change needs.',
  },
  testing: {
    term: 'Weak tests',
    definition: "Its tests miss the change's cases or pin its implementation.",
  },
  clarity: {
    term: 'Hard to read',
    definition: 'Unclear names, comments, or control flow.',
  },
};

/** The anchor's definition in a round that cannot rank the reference, naming both systems. */
export function anchorTerm(method: VotesMethod): Term {
  return {
    ...terms.anchor,
    definition: `${terms.anchor.definition} Its recorded score is ${formatScore(method.anchor_score)}.`,
  };
}

export function preliminaryTerm(rules: VotesRules): Term {
  return {
    ...terms.preliminary,
    definition: `Fewer than ${rules.preliminary_decisive_votes} raw votes with a winner. This marks coverage and does not measure confidence.`,
  };
}

export function agreementTerm(rules: VotesRules): Term {
  return {
    ...terms.raterAgreement,
    definition: `${terms.raterAgreement.definition} Below ${formatPercent(rules.rater_agreement_floor)} is flagged as low consistency.`,
  };
}

export function fallbackAnchor(
  anchorName: string,
  referenceName: string,
  method: VotesMethod,
): Term {
  return {
    ...terms.anchor,
    definition: `The most-voted system, ${anchorName}, fixed at ${formatScore(method.anchor_score)} in place of the reference ${referenceName}, which that round could not rank.`,
  };
}

/** What a round's `fingerprint` for a system identifies, quoting the hash. */
export function fingerprintTerm(fingerprint: string): Term {
  return {
    term: 'Fingerprint',
    definition: `${fingerprint}, a hash of the route, effort, harness, and prompt the system ran with that round.`,
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
