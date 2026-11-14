# Documentation

## Start Here

- [Quickstart](guide/quickstart.md): prepare a checkout and run smoke checks.
- [Voting](guide/voting.md): compare outputs on the public or private vote page.

## Use and Operate

- [Running](guide/running.md): configure credentials, generate and collect outputs, manage spend, publish votes and close rounds.
- [Authoring](guide/authoring.md): add families, cases, tasks and systems.
- [Data](conventions/data.md): choose where sources and runtime records belong.

## Understand Results

- [Systems](design/systems.md): generation identity, harnesses and route probes.
- [Ranking](design/ranking.md) and [Reports](design/reports.md): statistical methods, uncertainty and frozen round evidence.
- [Writing](design/families/writing.md) and [Coding](design/families/coding.md): measurement goals, source policies and limitations.
- [Arena](design/arena.md) and [Site](design/site.md): blind comparisons, publication boundaries and presentation.

## Modify the Implementation

Start with [`AGENTS.md`](../AGENTS.md), then the relevant area:

- [Development](conventions/development.md), [Testing](conventions/testing.md) and [CI](conventions/ci.md).
- [Python](conventions/python.md), [TypeScript](conventions/typescript.md) and [GitHub](conventions/github.md).
- [Architecture](design/architecture.md): module responsibilities, import layers and rerun contracts.
- [Harbor](research/harbor.md): upstream behavior that constrains integration changes.
