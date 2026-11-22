# Ranking

Votes families rank by point scores from pairwise votes, and tests and checkers families by [pass rates](#test-pass-rates).

The checkers scorer gives a case a binary pass only when every applicable rule passes. It uses the case as its sampling unit, with the same exact pass-rate interval and paired leader comparison as task tests. Per-rule rates retain their applicable case and response denominators and provide no partial credit. Each bare / prompted comparison reports the mean paired case-score difference and a descriptive 95% SciPy paired percentile bootstrap interval. A single case or constant paired differences yield no interval because the observed sample cannot estimate variation.

Every eligible [vote](arena.md#publication-and-closing) counts once against its original publication. A settled output pair has weight `1 / (samples_a * samples_b)` on its case. Reports freeze the method and reference. Exclusions and other uncounted votes contribute to `dropped_votes`. [Systems](systems.md#fingerprints) defines the configuration continuity required for pooling.

## Expected Net Points

Wins earn +1, losses earn −1, ordinary ties earn 0, and a both-bad outcome costs each system one point. A failing output loses to a passing one. Identical passing outputs tie, and two failing outputs both lose.

The relative Bradley–Terry fit counts ordinary ties as half a win and excludes both-bad rows. An identifiable left-pane intercept controls for display order and drops out of predictions. A second logistic fit estimates joint rejection with an additive effect for each system. Both fits shrink system coefficients with an L2 penalty of 0.01. The public score against the family's frozen reference is P(win) minus P(loss) minus P(both bad). The site shows it per 100 comparisons, and categories refit against the same reference.

A system needs a preference path to the reference and an identifiable joint rejection estimate for that matchup. Observed pairs must span the target contrast. When the available component has no joint rejections, the estimated rejection probability is zero. All published points are preliminary. The combined estimate has no case-level interval or leader decision.

## Archived Elo

Archived rounds use a Bradley–Terry score on an Elo scale. They treat ordinary ties and both-bad outcomes as half a win, cap the total vote weight per voter, and fix the anchor at the score recorded in the report. The anchor may differ from the configured reference when the reference lacked qualifying comparisons.

Archived intervals treat each case as one draw and describe new cases judged by the same voters. The archived metadata records the preliminary threshold and interval level. Reports may also show agreement across vote halves, length association, and differences between public and held-out cases.

## Test Pass Rates

Cost, token and latency aggregates cover error-free outputs and remain unknown unless every output in that group reports the metric, as [Reports](reports.md#results) describes.

The target is a system's pass rate on new tasks like the family's, so the task is the sampling unit and repeated trials of one task are clustered within it.

A trial passes when its tests reach a reward of 1, which they can after an agent error, since Harbor still runs the verifier. A trial whose tests never ran fails. A round closes only once every system has `samples` trials of every current task, so all systems are compared over the same tasks and trial counts. A system's pass rate is the mean over tasks of each task's share of passing trials, and categories apply the same rule to their own tasks.

### Interval

The 95% interval is the Clopper–Pearson interval (Clopper and Pearson, 1934, [The Use of Confidence or Fiducial Limits Illustrated in the Case of the Binomial](https://doi.org/10.1093/biomet/26.4.404)) for that mean, with the number of tasks as the sample size and the mean times the task count as the pass count, which is fractional when tasks run several trials. A task's share lies in [0, 1], and among such variables with a given mean the Bernoulli one has the largest variance (the Bhatia–Davis inequality), so the binomial variance at the task count bounds the clustered variance. With one trial per task it is the ordinary exact interval, whose coverage never falls below 95%. With several trials per task, simulations over 3 to 30 tasks, 1 to 5 trials, true rates from 0.3 to 0.99 and four dispersions of task difficulty kept its coverage at 96% or more.

Families hold few tasks, so methods that rely on a normal approximation or resampling fail there. Brown, Cai and DasGupta (2001, [Interval Estimation for a Binomial Proportion](https://doi.org/10.1214/ss/1009213286)) recommend the Wilson interval for samples of 40 or fewer, but document its undercoverage near 0 and 1, where coding pass rates often fall. With six tasks and a true rate of 0.97, Wilson covers 83%, so the board accepts the exact interval's extra width. Bowyer, Aitchison and Ivanova (2025, [Don't Use the CLT in LLM Evals With Fewer Than a Few Hundred Datapoints](https://arxiv.org/abs/2503.01747)) find that CLT and bootstrap intervals undercover in small LLM evaluations of independent questions. They do not evaluate intervals over clustered trials, so the clustered case rests on the variance bound and the simulations above. A percentile bootstrap over tasks collapses to a single point when every task passes, and an interval over trials treats repeats of one task as independent evidence, which narrows it without new information.

### pass^k

When a family runs `samples = k` trials of each task, pass^k is the share of tasks whose k trials all passed, which estimates the chance that k trials of a task all pass (Yao et al., 2024, [τ-bench](https://arxiv.org/abs/2406.12045)). It is null when k is 1.

### Leader Decision

The decision names the system with the top pass rate, the leader, and lists the systems an exact two-sided sign test (Dixon and Mood, 1946, [The Statistical Sign Test](https://doi.org/10.1080/01621459.1946.10501898)) cannot separate from it at the 5% level. The test pairs the two systems on their shared tasks and counts the tasks where each has the higher pass share, ignoring tasks where they agree. With one trial per task it is the exact McNemar test. It needs only independence between tasks, so it holds at any task count, and with six tasks the leader has to win all six that differ. When `tied` is empty, the claim that the leader beats every other system needs each of these tests to reject, so it holds at 5% without a multiplicity correction for a leader named in advance, although the board names the top rate. When `tied` is not empty, each separation from a system outside it is its own 5% test, with no correction across them.
