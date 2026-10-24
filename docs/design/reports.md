# Reports

A report publishes one closed round's aggregates under public system IDs. `report.py` defines the strict Pydantic schema, mirrored by the site's Zod schema.

## Closing a Round

`uv run bench close <family>` collects trials and closes the cumulative evaluation into `reports/<family>/<round>.json`. The round is the UTC closing date, so closing again that day replaces its file. Only this command writes reports, which are committed through ordinary PRs.

Closing requires current outputs and, for voted families, either eligible human choices or settled comparisons. A fully settled round can close with zero votes and sessions. Several fingerprints under one system ID, malformed prior reports or unverifiable retired identities fail closure with an explanation.

## Frozen Identity

`Report` holds `family`, `round`, `closed_at`, `systems` and `results`. `systems` maps exactly every ID referenced by the results to its public `display_name` and output `fingerprint`. An unknown fingerprint stays null. Standing rows reference this shared identity map. A historical round's labels and fingerprints never depend on today's catalog.

Current systems get labels from the resolved public catalog and fingerprints from actual outputs. A retired ID can reuse its latest prior report identity only when both fingerprints are known and match. Close a valid round before removing a runnable system. Different known fingerprints under the same ID fail continuity checks, while display names may change between rounds.

## Results

| Variant        | Published values                                                                                                                                                                                                                   |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `VotesResults` | Point standings and ranks, frozen method and rules, unavailable population inference, raw and effective human counts, output rates, cost and latency, distributions, defect tags, head-to-head records, categories and diagnostics |
| `TestsResults` | Trials, errored trials, descriptive pass rates, mean cost and p50 latency                                                                                                                                                          |

Voted intervals, best and worst ranks, separability and leader decisions are required null fields. Preliminary markers are derived from each round's raw decisive counts and recorded rule. [Ranking](ranking.md) defines the fit and uncertainty target.

Both scorers use current-case outputs. Cost and latency cover completed outputs without errors. Gate-failure rates cover outputs with a verdict, including errored task agents whose tests ran. Defect tags count once per choice on the tagged output. Errored trials count as failures in tests-scored pass rates.

## Publication Boundaries

Reports contain no case ID, instruction, output, route, request ID or session. Their only identifiers are categories, public system IDs, labels and configuration fingerprints. New scorers add a results variant, a closing arm and site views, as [Architecture](architecture.md#families-and-scorers) describes.

## Synthetic Fixtures

`tests/fixtures/report.json` and `report-early.json` are synthetic rounds shared by Python and site tests. [Testing](../conventions/testing.md#cross-checks-and-the-report-fixture) describes regeneration and schema comparison. Prettier leaves generated reports and fixtures as their writer formats them.
