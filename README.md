# assistant-bench

[![CI](https://github.com/hakula139/assistant-bench/actions/workflows/ci.yml/badge.svg)](https://github.com/hakula139/assistant-bench/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/hakula139/assistant-bench/graph/badge.svg)](https://codecov.io/gh/hakula139/assistant-bench)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A benchmark of AI assistant systems on everyday work, run on [Harbor](https://github.com/harbor-framework/harbor).

## Overview

A system combines a model with its effort, harness, and system prompt. Every system in a family receives the same instructions, and each family scores its outputs in one way: the owner's blind pairwise votes, or the tests of each Harbor task. Runs record tokens, cost, latency, and the exact system configuration behind every output.

Early development. What works today:

- **Systems:** declared in [`systems.toml`](systems.toml) and resolved against the model catalog and assistant instructions of [nixos-config](https://github.com/hakula139/nixos-config), each with a fingerprint of what it sends
- **Families:** `prose-zh` and `prose-en`, with 30 committed writing cases each and gates that reject empty, truncated, wrong-language, or Traditional-script outputs, plus the `smoke` and `smoke-text` families that check the setup
- **Runs:** one chat-completions call per trial through any OpenAI-compatible endpoint, with reruns that generate only missing trials
- **Outputs:** one record per trial with the text or reward, token counts, cost at catalog prices, latency, and provenance
- **Arena:** a local vote page serving blind pairs, with a balanced seed, adaptive pairing, swapped repeats, and automatic losses for outputs that fail a gate
- **Reports:** Bradley-Terry scores with case-clustered bootstrap intervals and rank spreads, written as aggregate JSON under `reports/`
- **Site:** static leaderboards, category breakdowns, and cost and latency charts built from the committed reports

## Usage

Requires Nix with flakes, a running Docker daemon, and on NixOS [nix-ld](https://github.com/nix-community/nix-ld), as the [quickstart](docs/guide/quickstart.md#requirements) lists:

```bash
nix develop                                 # Dev shell with every tool
uv sync                                     # Virtualenv with Harbor and the bench
cp .env.example .env                        # Then fill in the endpoint variables
uv run bench check smoke                    # Run Harbor's oracle on the smoke task
uv run bench run smoke-text                 # Send each smoke case to every listed system
uv run bench ingest smoke-text              # Collect the replies into the data directory
uv run bench serve                          # Vote on blind pairs at http://127.0.0.1:8790/vote/
uv run bench close smoke-text               # Write the family's report under reports/
```

The [quickstart](docs/guide/quickstart.md) walks through the setup, and [Running](docs/guide/running.md) covers the endpoint, reruns, voting, closing a round, and the data directory.

## Documentation

- [User guide](docs/guide/): quickstart, running a family, voting, and authoring cases, families, and systems
- [Design](docs/design/): architecture, systems, the arena, ranking, reports, and the prose families
- [Conventions](docs/conventions/) and [`AGENTS.md`](AGENTS.md): how changes are written and checked

## License

[MIT](LICENSE)
