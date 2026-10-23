# Design

Architecture decisions for assistant-bench. Each page covers the current design of one topic, building on the findings in [`docs/research/`](../research/).

| Document                        | Description                                                                                |
| ------------------------------- | ------------------------------------------------------------------------------------------ |
| [Architecture](architecture.md) | Module map, import layers, families and scorers, data flow, reruns                         |
| [Systems](systems.md)           | `systems.toml`, fingerprints, harnesses, the nixos-config export, concurrency              |
| [Arena](arena.md)               | Blinding, sessions and pair scheduling, the vote store, the vote API, the render origin    |
| [Ranking](ranking.md)           | Bradley-Terry with ties, the clustered bootstrap, rank spread, the decision, diagnostics   |
| [Reports](reports.md)           | Closing a round, the report schema and its scorer variants, what is published, the fixture |
| [Site](site.md)                 | The results pages, the vote page, the charts, the design direction                         |
| [Writing](families/writing.md)  | `writing-zh` and `writing-en`: systems, categories, case sourcing, gates, verdicts         |
