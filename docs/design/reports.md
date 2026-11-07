# Reports

A report publishes one closed round's aggregates under public system IDs. `report.py` defines the strict Pydantic schema, mirrored by the site's Zod schema.

## Closing a Round

`uv run bench close <family>` closes a round into `reports/<family>/<round>.json`. A voted family first saves a cut of its counted votes, which `--snapshot <path>` recloses later, while a test-scored family collects and aggregates its current trials. The round is the UTC closing date, so closing again that day replaces its file. Reports are committed through ordinary PRs.

Voted closure uses the cut's active publication, the collected measurements and the current family's labels. It requires eligible human choices or settled comparisons. A fully settled round can close with zero votes and voters. Several fingerprints under one system ID, or a malformed prior report, fail closure with an explanation.

## Frozen Identity

`Report` holds `family`, `round`, `closed_at`, `systems` and `results`. `systems` maps exactly every ID referenced by the results to its public `display_name` and output `fingerprint`. An unknown fingerprint stays null. Standing rows reference this shared identity map. A historical round's labels and fingerprints never depend on today's catalog.

Voted systems get labels, fingerprints and the reference from the publication the closing cut read. Test-scored systems get labels from the resolved public catalog and fingerprints from actual outputs. A known fingerprint that differs from the one an earlier report froze under the same ID fails closure, while display names may change between rounds.

## Results

`CheckersResults` records strict case pass rates and exact intervals, response and error counts, categories, the leader decision, applicable-rule diagnostics, and descriptive bare / prompted effects with nullable paired bootstrap intervals. It publishes only aggregates under system names. The grader revision identifies the collected checks independently of generation fingerprints.

| Variant        | Published values                                                                                                                                                                                                                                                                                                                                                                                                                    |
| -------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `VotesResults` | Point standings with their score intervals and ranks, the method's parameters, interval level and actor cap, the preliminary rule, the leader decision, production's vote cutoff with the counted votes up to it, raw and effective human counts, voters, cases, output rates, a pass rate with its 95% interval for tested outputs, cost and latency, distributions, defect tags, head-to-head records, categories and diagnostics |
| `TestsResults` | Samples per task, then per system its tasks, trials, errored trials, pass rate with its 95% interval, pass^k, mean cost and latency percentiles, per-category pass rates, and the leader decision                                                                                                                                                                                                                                   |

Preliminary markers are derived from each round's interval widths and recorded rule. In a head-to-head row, `wins`, `losses`, `ties` and `both_bad` are raw counts, while `win_rate` uses actor weights. [Ranking](ranking.md) defines the fit, the score intervals and the votes leader decision, and its [test pass rates](ranking.md#test-pass-rates) define the tests intervals and leader decision.

Each round's output inventory determines its measurement denominators. Cost, latency and token aggregates cover completed outputs without errors, and each aggregate is null unless every such output reports its value. A null mean cost records `cost_gap`: `unpriced` when an output with known usage lacks a price, or `unreported` when only outputs without usage lack a cost. A system without completed outputs has no cost gap. Gate-failure rates cover outputs with a verdict, including errored task agents whose tests ran. Defect tags count once per choice on the tagged output. A votes standing carries the [pass rate](ranking.md#test-pass-rates) with its 95% interval when the system's outputs have test rewards, and null otherwise.

## Publication Boundaries

Reports contain no case ID, instruction, output, route, request ID or session. Their only identifiers are categories, public system IDs, labels and configuration fingerprints. New scorers add a results variant, a closing arm and site views, as [Architecture](architecture.md#families-and-scorers) describes.

## Synthetic Fixtures

Python and site tests share synthetic report fixtures, which [Testing](../conventions/testing.md#cross-checks-and-the-report-fixture) describes. Prettier leaves generated reports and fixtures as their writer formats them.
