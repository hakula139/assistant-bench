# Reports

A report is the published record of one closed round of a family: aggregates under public system names, which the site renders and nothing else reads. `report.py` defines its pydantic schema and writes it.

## Closing a Round

`uv run bench close <family>` collects the family's trials and aggregates everything it has so far, then writes `reports/<family>/<round>.json`, where the round is the UTC date of closing, so closing again on the same day replaces that day's file. Only `bench close` writes under `reports/`, and each report is committed through an ordinary PR. A board pools every round, so each report is a snapshot of the cumulative board at its date.

Closing fails, naming the problem, when the family has no outputs on its current cases, when a family scored by votes has no votes on its current outputs, or when a system's current outputs carry several fingerprints.

## Schema

`Report` holds the family, the round, the closing time, and `results`, a union discriminated by `scorer` with one variant per scorer:

| Variant        | Holds                                                                                                                                                                                                                                                                                                                                                                               |
| -------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `VotesResults` | Standings with scores, intervals, rank spread, vote counts, both-bad and gate-failure rates, cost, latency, token and latency percentiles, and defect tallies, the family's reference and the round's anchor, plus the counted and dropped votes, the unranked systems, head-to-head records, category boards, the diagnostics, and the decision that [Ranking](ranking.md) defines |
| `TestsResults` | Per system, the trials, the errored trials, the pass rate with an errored trial counting as a failure, the mean cost, and the p50 latency                                                                                                                                                                                                                                           |

Each defect the family lists counts against the output the owner tagged with it, once per vote, whichever side won, and the tallies follow the family's `defects` order. Both scorers count only the outputs on the family's current cases, and costs and latencies cover the outputs among them that finished without an error, while the gate-failure rate covers the outputs with a verdict, which on a task family includes a solution whose agent errored but whose tests still ran. A system without a counted vote still ranks through its settled results, with no both-bad rate, and `decision` is null while a system tied with the top one is preliminary. `anchor` is the reference when the round ranks it, and otherwise the most-voted system that stands at 1,000. Rounds with different anchors are offset by an unknown constant, so compare scores only within one round.

A new scorer adds one variant here, an arm in `close`, and a board on the site, as [Architecture](architecture.md#families-and-scorers) describes.

## What Is Published

A report never holds a case ID, an instruction, an output, a route, a request ID, or a session, so private cases can enter a board without leaving the data directory. Categories, system names, and fingerprints, which hash a system's configuration, are its only identifiers.

## The Fixture

`tests/fixtures/report.json` is a report closed from about 300 simulated votes on eight generic systems over ten sessions, which the site's tests parse and render in place of real reports, as they do the early fixture. It has swapped repeats, settled gate failures, votes dropped on a retired case, two unranked systems that meet only each other, and a preliminary system ranked only through settled results, with no both-bad rate. `tests/fixtures/report-early.json` closes the first session alone, when every system is still preliminary and `decision` is null. [Testing](../conventions/testing.md#cross-checks-and-the-report-fixture) covers the tests that keep each fixture and the schema current. Prettier leaves `reports/` and the fixtures as the writer formats them, so a rewritten file never needs reformatting.
