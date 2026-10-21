# Ranking

`scoring/ranking.py` turns a family's votes into scores, intervals, and rank spreads, and `report.py` adds the diagnostics and the decision a closed round reports. The family's current case set defines the benchmark, and every vote that [reconciles](arena.md#sessions-and-pairs) with its current outputs enters one cumulative fit, since the pooling rule in [Systems](systems.md#fingerprints) keeps each system name on one fingerprint. The report counts the other votes as `dropped_votes`.

## The Fit

Each vote compares the left system A with the right system B, and its outcome for A is 1 for `a`, 0 for `b`, and 0.5 for `tie` and `both_bad`. The results the outputs settle without a vote, which [Arena](arena.md#sessions-and-pairs) defines, enter beside the votes with their fractional weights, among the systems with recorded votes. Each system's both-bad rate over the owner's votes is reported beside its score, as the floor that a relative score cannot show, and its gate-failure rate beside that.

The fit is Bradley-Terry by weighted logistic loss, minimized with L-BFGS:

- Each row is weighted by the share of a vote it counts, and its design holds +1 for A's system and −1 for B's, plus a left-side intercept that absorbs position bias and is reported as `position`. A settled result holds 0 in the intercept column, since no pane showed it.
- An L2 penalty of 0.01 on the system strengths keeps a system that wins every vote finite, without penalizing the intercept.
- Strengths are centered at mean zero, and a fit that fails to converge raises.
- A test fits 3,000 simulated votes with fractional outcomes and a covariate, and checks the strength gaps, the intercept, and the coefficient against statsmodels' `Logit` within 0.002.

## Scores and Intervals

Scores sit on an Elo-like scale, 400 / ln 10 points per logit, with the family's `reference` system at 1000.

Strengths compare only within one connected component of the comparison graph, because the penalty centers each component on its own. The board, each category board, and each half of the split-half fit therefore keep the systems that comparisons connect to the reference, found with SciPy's `connected_components`, and the report lists the other voted systems as `unranked`, without a score, together with the systems whose interval is unbounded.

Intervals come from 1,000 bootstrap resamples clustered by case, because votes on one case are correlated: each resample draws cases with replacement and takes all of a drawn case's votes. Votes of each ordered pair and sidedness collapse into one weighted row, so a refit stays cheap, and a fixed seed makes closing the same votes twice write the same intervals. Each resample's strengths keep mean zero and shift by the point fit's reference strength, so the reference has an interval like every other system. The interval is the 2.5th to 97.5th percentile, with each bound rounded outward to the nearest resample. A resample whose drawn cases leave a system unconnected to the reference gives it no score, which counts as −∞ for the lower bound and +∞ for the upper, so a system detached in more than 2.5% of resamples has an unbounded interval. A perfect record on a few cases is one such system.

## Rank Spread

Following LMArena, each system's best rank is 1 plus the number of systems whose lower bound exceeds its upper bound, and its worst rank is 1 plus the number of other systems whose upper bound exceeds its lower bound. `rank` itself orders the point scores. A system with fewer than 40 decisive votes, which are the votes won by either side, is `preliminary`.

## Decision

A ranked system ties with the top-scoring one when the 95% interval of its score's difference from the top's contains 0, where the difference pairs the two scores within each bootstrap resample, and an unbounded difference counts as a tie. While any system in that tie set is preliminary, the report has no decision, so a system with few votes neither takes the lead nor loses a tie-break before its interval narrows. A tie set of one is `separated`. Otherwise the tie goes to the lower mean cost per output, then to the lower p50 latency, where a known value beats a missing one, and finally to the higher score. The report records which criterion decided.

## Diagnostics

| Field             | Meaning                                                                                                                                                                                                 |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `position`        | The left pane's advantage in score points                                                                                                                                                               |
| `length`          | The score change per standard deviation of (A − B) / (A + B) over output lengths, from one refit of the owner's votes with that covariate. It only describes the board, which the raw score still ranks |
| `separability`    | The share of system pairs whose intervals do not overlap                                                                                                                                                |
| `split_half_tau`  | Kendall's τ between the orders fit to the votes of odd and even sessions, over the systems both halves connect to the reference, and absent unless both halves include it                               |
| `rater_agreement` | How often a side-swapped repeat matched its original, where a tie matches a tie. `below_floor` marks agreement under 70%, when separations are weaker than the intervals suggest                        |
| `categories`      | A refit and bootstrap per case category, anchored at the reference, with its own `unranked` list. A category whose votes never include the reference has no board                                       |

Lengths count each Han character and each other whitespace-separated run as one unit, which measures Chinese in characters and English in words, including text that mixes both.
