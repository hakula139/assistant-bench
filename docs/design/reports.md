# Reports

A report publishes one closed round's aggregates under the recorded configuration keys of its family. The keys distinguish conditions that share a system ID, as described in [Systems](systems.md#retained-trial-identities). [`report.py`](../../src/assistant_bench/report.py) defines the strict Pydantic schema, mirrored by the site's Zod schema. [Testing](../conventions/testing.md#cross-checks-and-the-report-fixture) describes their shared synthetic fixtures. Prettier leaves generated reports and fixtures as their writer formats them.

## Closing a Round

`uv run bench close <family>` closes a round into `reports/<family>/<round>.json`. A voted family seals cuts of its available production and private votes in private D1. `--snapshot <id>` replays that immutable snapshot with its recorded scoring method and closing time. A test-scored family aggregates a collected generation with its pinned sources. The round is the UTC closing date. Closing again that day can update its report under the same method.

Voted closure counts ballots from their original publications and measures outputs from the active cuts' pinned generations. Missing historical generation evidence leaves measurements unknown. Replay needs no current family, case sources or catalog. Closure requires eligible recorded votes or settled comparisons. A fully settled round can close with zero votes and voters. Several fingerprints under one system ID, or a malformed prior report, fail closure with an explanation.

## Frozen Identity

A historical round freezes every referenced system's public label and output fingerprint. An unknown fingerprint stays null. Historical identity never depends on today's catalog.

Voted systems get labels, fingerprints and the reference from the publication the closing cut read. Test-scored systems get labels from the catalog names frozen by collection and fingerprints from that generation's outputs. A known fingerprint that differs from the one an earlier report froze under the same ID fails closure, while display names may change between rounds.

## Results

The [schema](../../src/assistant_bench/report.py) defines each scorer's aggregates. The grader revision identifies collected checks independently of generation fingerprints.

In head-to-head rows, `wins`, `losses`, `ties` and `both_bad` are raw counts. Expected net rounds give ties half credit in `win_rate`, while double losses give zero. Archived Elo rounds use their recorded weights and give half credit for both outcomes. [Ranking](ranking.md) defines the scores and preliminary markers.

Each round's output inventory determines its measurement denominators. Cost, latency and token aggregates cover completed outputs without errors, and each aggregate is null unless every such output reports its value. A null mean cost records `cost_gap`: `unpriced` when an output with known usage lacks a price, or `unreported` when only outputs without usage lack a cost. A system without completed outputs has no cost gap. Gate-failure rates cover outputs with a verdict, including errored task agents whose tests ran. Defect tags count once per choice on the tagged output. A votes standing carries the [pass rate](ranking.md#test-pass-rates) with its 95% interval when the system's outputs have test rewards, and null otherwise.

## Publication Boundaries

Reports contain no case ID, instruction, output, route, request ID or session. Their only identifiers are categories, recorded configuration keys, labels and configuration fingerprints. [Architecture](architecture.md#families-and-scorers) describes adding a scorer.
