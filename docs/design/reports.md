# Reports

A report publishes one closed round's aggregates under public system IDs. `report.py` defines the strict Pydantic schema, mirrored by the site's Zod schema.

## Closing a Round

`uv run bench close <family> --snapshot <path>` closes a saved canonical D1 export into `reports/<family>/<round>.json`. Test-scored families use `uv run bench close <family>` to collect and aggregate current trials. The round is the UTC closing date, so closing again that day replaces its file. Only this command writes reports, which are committed through ordinary PRs.

Voted closure uses the export's complete frozen inventory, measurements and labels. It requires eligible human choices or settled comparisons. A fully settled round can close with zero votes and sessions. Several fingerprints under one system ID, malformed prior reports or unverifiable retired identities fail closure with an explanation.

## Frozen Identity

`Report` holds `family`, `round`, `closed_at`, `systems` and `results`. `systems` maps exactly every ID referenced by the results to its public `display_name` and output `fingerprint`. An unknown fingerprint stays null. Standing rows reference this shared identity map. A historical round's labels and fingerprints never depend on today's catalog.

Voted systems get labels and fingerprints from the sealed publication and evaluation. Test-scored systems get labels from the resolved public catalog and fingerprints from actual outputs. A retired ID can reuse its latest prior report identity only when both fingerprints are known and match. Different known fingerprints under the same ID fail continuity checks, while display names may change between rounds.

## Results

| Variant        | Published values                                                                                                                                                                                                                   |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `VotesResults` | Point standings and ranks, frozen method and rules, unavailable population inference, raw and effective human counts, output rates, cost and latency, distributions, defect tags, head-to-head records, categories and diagnostics |
| `TestsResults` | Samples per task, then per system its tasks, trials, errored trials, pass rate with its 95% interval, pass^k, mean cost and p50 latency, per-category pass rates, and the leader decision                                          |

Voted intervals, best and worst ranks, separability and leader decisions are required null fields. Preliminary markers are derived from each round's raw decisive counts and recorded rule. [Ranking](ranking.md) defines the fit and uncertainty target, and its [test pass rates](ranking.md#test-pass-rates) define the tests intervals and leader decision.

Each round's output inventory determines its measurement denominators. Cost and latency cover completed outputs without errors. Gate-failure rates cover outputs with a verdict, including errored task agents whose tests ran. Defect tags count once per choice on the tagged output. Tests-scored pass rates count every trial, and a trial whose tests never ran fails. A tests round closes only once every system has `samples` trials of every current task.

## Publication Boundaries

Reports contain no case ID, instruction, output, route, request ID or session. Their only identifiers are categories, public system IDs, labels and configuration fingerprints. New scorers add a results variant, a closing arm and site views, as [Architecture](architecture.md#families-and-scorers) describes.

## Synthetic Fixtures

`tests/fixtures/report.json` and `report-early.json` are synthetic votes rounds and `report-tests.json` a synthetic tests round, shared by Python and site tests. [Testing](../conventions/testing.md#cross-checks-and-the-report-fixture) describes regeneration and schema comparison. Prettier leaves generated reports and fixtures as their writer formats them.
