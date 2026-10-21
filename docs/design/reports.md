# Reports

A report is the published record of one closed round of a family: aggregates under public system names, which the site renders and nothing else reads. `report.py` defines its pydantic schema and writes it.

## Closing a Round

`uv run bench close <family>` aggregates everything the family has so far and writes `reports/<family>/<round>.json`, where the round is the UTC date of closing, so closing again on the same day replaces that day's file. Only `bench close` writes under `reports/`, and each report is committed through an ordinary PR. A board pools every round, so each report is a snapshot of the cumulative board at its date.

Closing fails, naming the problem, when the family has nothing to report, when a family scored by votes has no votes on its reference system, or when one system name carries several fingerprints.

## Schema

`Report` holds the family, the round, the closing time, and `results`, a union discriminated by `scorer` with one variant per scorer:

| Variant        | Holds                                                                                                                                                                                                                                                                                                                              |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `VotesResults` | Standings with scores, intervals, rank spread, vote counts, both-bad and gate-failure rates, cost, latency, token and latency percentiles, and flag tallies, plus the counted and dropped votes, the unranked systems, head-to-head records, category boards, the diagnostics, and the decision that [Ranking](ranking.md) defines |
| `TestsResults` | Per system, the trials, the errored trials, the pass rate with an errored trial counting as a failure, the mean cost, and the p50 latency                                                                                                                                                                                          |

Flags count against the side a vote did not prefer, and against both sides of a tie or a both-bad vote. Costs and latencies average every output of the system on the current cases that finished without an error, and the gate-failure rate covers the outputs the gates checked. A system whose votes all dropped out still ranks through its settled results, with no both-bad rate, and `decision` is null while a system tied with the top one is preliminary.

A new scorer adds one variant here, an arm in `close`, and a board on the site, as [Architecture](architecture.md#families-and-scorers) describes.

## What Is Published

A report never holds a case ID, an instruction, an output, a route, a request ID, or a session, so private cases can enter a board without leaving the data directory. Categories, system names, and fingerprints, which hash a system's configuration, are its only identifiers.

## The Fixture

`tests/fixtures/report.json` is a report closed from about 300 simulated votes on five generic systems over ten sessions, which the site build loads in place of real reports. It has swapped repeats, settled gate failures, votes dropped on a retired case, two unranked systems that meet only each other, and a preliminary system ranked only through settled results, with no both-bad rate. `tests/fixtures/report-early.json` closes the first session alone, when every system is still preliminary and `decision` is null. `test_report.py` closes the same simulated rounds and fails when a committed fixture differs from them beyond float precision, rewriting the fixture for review, and it also validates the committed files against `Report.model_json_schema()`. Prettier leaves `reports/` and the fixtures as the writer formats them, so a rewritten file never needs reformatting.
