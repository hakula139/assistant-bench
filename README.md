# assistant-bench

[![CI](https://github.com/hakula139/assistant-bench/actions/workflows/ci.yml/badge.svg)](https://github.com/hakula139/assistant-bench/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/hakula139/assistant-bench/graph/badge.svg)](https://codecov.io/gh/hakula139/assistant-bench)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A benchmark of AI assistant systems on everyday work, run on [Harbor](https://github.com/harbor-framework/harbor), with leaderboards published at [bench.hakula.xyz](https://bench.hakula.xyz) as rounds close.

## Overview

A system combines a model with its effort, harness, and system prompt. Every system in a family receives the same instructions, and the family scores their outputs either by the owner's blind pairwise votes or by each Harbor task's tests. Every output records its tokens, cost, latency, and the exact system configuration behind it.

Early development. The first families, `prose-zh` and `prose-en`, rank how systems write everyday technical prose in Chinese and in English.

## Usage

Requires Nix with flakes (and nix-ld on NixOS), a running Docker daemon, and an OpenAI-compatible endpoint, as the [quickstart](docs/guide/quickstart.md#requirements) lists:

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

## Documentation

- [User guide](docs/guide/): setup, running and voting on a family, and authoring cases, families, and systems
- [Design](docs/design/): how systems, the arena, ranking, reports, and each family work
- [`AGENTS.md`](AGENTS.md): the entry point for contributors, linking the [conventions](docs/conventions/)

## License

[MIT](LICENSE)
