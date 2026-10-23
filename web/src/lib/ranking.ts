// The ranking settings the site states, mirroring the Python ranking and report modules, kept
// apart from the forest axis so the glossary and the method strip load without its dependencies.

/** The score every family's and category's anchor is fixed at. Mirrors `ranking.ANCHOR`. */
export const ANCHOR_SCORE = 1000;

/**
 * The decisive votes a system needs to stop being preliminary. Mirrors
 * `report.PRELIMINARY_DECISIVE_VOTES`.
 */
export const PRELIMINARY_VOTES = 40;

/**
 * The rater agreement below which a round warns that its intervals overstate separation. Mirrors
 * `report.RATER_AGREEMENT_FLOOR`.
 */
export const AGREEMENT_FLOOR = 0.7;

/** The bootstrap resamples behind each 95% interval. Mirrors `ranking.RESAMPLES`. */
export const RESAMPLES = 1000;
