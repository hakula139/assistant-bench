# Ranking

`scoring/ranking.py` fits a family's point scores. `report.py` records them with the method, rules and descriptive diagnostics. Every vote that [reconciles](arena.md#sessions-and-pairs) with the export's frozen approved inventory enters the cumulative evaluation. Other votes count as `dropped_votes`. [Systems](systems.md#fingerprints) defines the configuration continuity required for pooling.

## The Fit

Each row compares system A with B. Its outcome for A is 1 for `a`, 0 for `b`, and 0.5 for `tie` and `both_bad`. An actor with `n` eligible ordinary choices receives weight `min(1, 100 / n)` on each choice. These weights are assigned across the complete family evaluation before selecting components, categories or diagnostics. Swapped repeats measure consistency and carry zero fitting weight. Each report records its weighting method, including unit weights in existing closed rounds.

A settled output pair has weight `1 / (samples_a * samples_b)` on its case. Listed systems and systems with recorded votes can rank through settled comparisons even without a human choice.

The weighted Bradley-Terry logistic objective uses +1 for A and −1 for B. Human rows also supply an unpenalized left-side intercept. Settled rows have no sided intercept. An L2 penalty of 0.01 on system strengths keeps unanimous point estimates finite. Strengths are centered at zero, and optimizer failure raises an error.

## Point Scores

Scores use 400 / ln 10 points per logit. The fit selects the component connected to the system with the greatest effective human weight, with deterministic ordering for equal weights. The family's reference anchors the board when it belongs to that component. Otherwise the root anchors it. The anchor is fixed at 1,000, which shifts every point equally and preserves gaps and order. These settings and the actor cap are constants in `scoring/`, and the rules are constants in `report.py`. Publication seals them into each release, closing refuses an export sealed under other values, and reports record them in `method` and `rules`.

Only comparisons connect strengths. Every entrant in the selected component keeps its point, including a sparse unanimous entrant. Other eligible systems are `unranked`. Categories refit their own comparisons in the overall anchor's component and retain its anchor score. A category without that anchor has no ranked rows.

`rank` is descriptive competition order by point score. The site derives a preliminary coverage marker from `decisive_votes < rules.preliminary_decisive_votes`. The current threshold is 40 raw choices with a winner. It supplies no independent-voter count or confidence claim.

## Uncertainty

The target is performance on new cases and voters. Population uncertainty is unavailable until a suitable method is calibrated. Reports record `inference.status = "unavailable"`, target `"new-cases-and-voters"` and reason `"population-method-not-validated"`.

Intervals, best and worst ranks, separability and leader decisions are explicitly null. The strict report schema rejects non-null claims for this method state. Point scores and ranks remain available.

## Counts and Diagnostics

`votes` and `decisive_votes` are raw eligible ordinary choice counts. `effective_votes` sums their actor weights and excludes settlements. Overall totals include eligible choices across all components. Each participating system counts a choice, so standing totals can sum to twice the overall mass. Category counts use that category's choices with their original weights. Repeats and inactive choices remain in the accepted denominator and contribute to `dropped_votes`.

| Field             | Meaning                                                                                                                                                          |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `position`        | Fitted left-pane advantage in score points, null without human sided rows in the ranked component                                                                |
| `length`          | Score change per standard deviation of (A − B) / (A + B) over output lengths in ranked human rows. Null when constant or confounded with system or pane identity |
| `split_half_tau`  | Kendall's τ between odd- and even-session point orders over systems both halves connect to their root                                                            |
| `rater_agreement` | Agreement of swapped repeats with their originals. `below_floor` uses the report's recorded repeat-consistency floor, currently 0.7                              |

Lengths count each Han character and each other whitespace-separated run as one unit. Length association and repeat consistency describe the observed evidence and supply no population interval.
