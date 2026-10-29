# Ranking

Votes families rank by point scores from pairwise votes, and tests families by [pass rates](#test-pass-rates).

`scoring/ranking.py` fits a family's point scores. `report.py` records them with the method, rules and descriptive diagnostics. Every vote that [counts](arena.md#publication-and-closing) against the cut's active publication enters the cumulative evaluation. Excluded and other uncounted votes count as `dropped_votes`. [Systems](systems.md#fingerprints) defines the configuration continuity required for pooling.

## The Fit

Each row compares system A with B. Its outcome for A is 1 for `a`, 0 for `b`, and 0.5 for `tie` and `both_bad`. An actor with `n` eligible ordinary choices receives weight `min(1, 100 / n)` on each choice. These weights are assigned across the complete family evaluation before selecting components, categories or diagnostics. Each report records the cap as `method.actor_cap`.

A settled output pair has weight `1 / (samples_a * samples_b)` on its case. Listed systems and systems with recorded votes can rank through settled comparisons even without a human choice.

The weighted Bradley-Terry logistic objective uses +1 for A and −1 for B. Human rows also supply an unpenalized left-side intercept. Settled rows have no sided intercept. An L2 penalty of 0.01 on system strengths keeps unanimous point estimates finite. Strengths are centered at zero, and optimizer failure raises an error.

## Point Scores

Scores use 400 / ln 10 points per logit. The fit selects the component connected to the system with the greatest effective human weight, with deterministic ordering for equal weights. The family's reference anchors the board when it belongs to that component. Otherwise the root anchors it. The anchor is fixed at 1,000, which shifts every point equally and preserves gaps and order. These settings and the actor cap are constants in `scoring/`, and the rules are constants in `report.py`. Reports record them in `method` and `rules`.

Only comparisons connect strengths. Every entrant in the selected component keeps its point, including a sparse unanimous entrant. Other eligible systems are `unranked`. Categories refit their own comparisons in the overall anchor's component and retain its anchor score. A category without that anchor has no ranked rows.

`rank` is descriptive competition order by point score. The site derives a preliminary coverage marker from `decisive_votes < rules.preliminary_decisive_votes`. The current threshold is 40 raw choices with a winner. It supplies no independent-voter count or confidence claim.

## Uncertainty

The target is performance on new cases and voters. No method for that target is calibrated yet, so voted reports publish point scores and ranks without intervals or leader decisions.

## Counts and Diagnostics

`votes` and `decisive_votes` are raw eligible ordinary choice counts. `effective_votes` sums their actor weights and excludes settlements. Overall totals include eligible choices across all components. Each participating system counts a choice, so standing totals can sum to twice the overall mass. Category counts use that category's choices with their original weights. Excluded votes and votes on outputs that no longer count stay out of the fit and count in `dropped_votes`.

| Field            | Meaning                                                                                                                                                          |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `position`       | Fitted left-pane advantage in score points, null without human sided rows in the ranked component                                                                |
| `length`         | Score change per standard deviation of (A − B) / (A + B) over output lengths in ranked human rows. Null when constant or confounded with system or pane identity |
| `split_half_tau` | Kendall's τ between the point orders fit to alternating votes, ordered by store and ID, over systems both halves connect to their root                           |

Lengths count each Han character and each other whitespace-separated run as one unit. Length association and split-half agreement describe the observed evidence and supply no population interval.

## Test Pass Rates

`scoring/pass_rates.py` holds the statistics that `report.py` applies to a tests family's trials. The target is a system's pass rate on new tasks like the family's, so the task is the sampling unit and repeated trials of one task are clustered within it.

A trial passes when its tests reach a reward of 1, which they can after an agent error such as a timeout, since Harbor still runs the verifier. A trial whose tests never ran fails. A round closes only once every system has `samples` trials of every current task, so all systems are compared over the same tasks and trial counts. A system's pass rate is the mean over tasks of each task's share of passing trials, and categories apply the same rule to their own tasks.

### Interval

The 95% interval is the Clopper–Pearson interval (Clopper and Pearson, 1934, [The Use of Confidence or Fiducial Limits Illustrated in the Case of the Binomial](https://doi.org/10.1093/biomet/26.4.404)) for that mean, with the number of tasks as the sample size and the mean times the task count as the pass count, which is fractional when tasks run several trials. A task's share lies in [0, 1], and among such variables with a given mean the Bernoulli one has the largest variance (the Bhatia–Davis inequality), so the binomial variance at the task count bounds the clustered variance. With one trial per task it is the ordinary exact interval, whose coverage never falls below 95%. With several trials per task, simulations over 3 to 30 tasks, 1 to 5 trials, true rates from 0.3 to 0.99 and four dispersions of task difficulty kept its coverage at 96% or more.

Families hold few tasks, so methods that rely on a normal approximation or resampling fail there. Brown, Cai and DasGupta (2001, [Interval Estimation for a Binomial Proportion](https://doi.org/10.1214/ss/1009213286)) recommend the Wilson interval for samples of 40 or fewer, but document its undercoverage near 0 and 1, where coding pass rates often fall. With six tasks and a true rate of 0.97, Wilson covers 83%, so the board accepts the exact interval's extra width. Bowyer, Aitchison and Ivanova (2025, [Don't Use the CLT in LLM Evals With Fewer Than a Few Hundred Datapoints](https://arxiv.org/abs/2503.01747)) find that CLT and bootstrap intervals undercover in small LLM evaluations of independent questions. They do not evaluate intervals over clustered trials, so the clustered case rests on the variance bound and the simulations above. A percentile bootstrap over tasks collapses to a single point when every task passes, and an interval over trials treats repeats of one task as independent evidence, which narrows it without new information.

### pass^k

When a family runs `samples = k` trials of each task, pass^k is the share of tasks whose k trials all passed, which estimates the chance that k trials of a task all pass (Yao et al., 2024, [τ-bench](https://arxiv.org/abs/2406.12045)). It is null when k is 1.

### Leader Decision

The decision names the system with the top pass rate, the leader, and lists the systems an exact two-sided sign test (Dixon and Mood, 1946, [The Statistical Sign Test](https://doi.org/10.1080/01621459.1946.10501898)) cannot separate from it at the 5% level. The test pairs the two systems on their shared tasks and counts the tasks where each has the higher pass share, ignoring tasks where they agree. With one trial per task it is the exact McNemar test. It needs only independence between tasks, so it holds at any task count, and with six tasks the leader has to win all six that differ. When `tied` is empty, the claim that the leader beats every other system needs each of these tests to reject, so it holds at 5% without a multiplicity correction for a leader named in advance, although the board names the top rate. When `tied` is not empty, each separation from a system outside it is its own 5% test, with no correction across them.
