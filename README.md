# assistant-bench

[![CI](https://github.com/hakula139/assistant-bench/actions/workflows/ci.yml/badge.svg)](https://github.com/hakula139/assistant-bench/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/hakula139/assistant-bench/graph/badge.svg)](https://codecov.io/gh/hakula139/assistant-bench)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![WakaTime coding time for assistant-bench](https://wakatime.com/badge/user/f4a35a1f-0e29-4093-a647-e66aad164737/project/cbd2e36f-3ab4-4fc6-b6b9-840bd73f7594.svg)

Leaderboards for AI models and agents, ranked through blind pairwise voting or automated tests. [Harbor](https://github.com/harbor-framework/harbor) runs the benchmark, and [bench.hakula.xyz](https://bench.hakula.xyz) publishes the results and hosts public voting.

## Overview

A system combines a model with its effort, harness, and system prompt. Every system in a family receives the same instructions, and the family scores its outputs through public blind pairwise voting or each Harbor task's tests. Every output records its tokens, cost, latency, and the fingerprint of the system configuration behind it.

Early development. The first families, `writing-zh` and `writing-en`, rank how systems write everyday prose in Chinese and in English, from technical documents and messages to blog posts, essays, reviews, and fiction.

## Usage

Vote at [bench.hakula.xyz/vote/](https://bench.hakula.xyz/vote/). Running the benchmark requires Nix with flakes (and nix-ld on NixOS), a running Docker daemon for task families, and an OpenAI-compatible endpoint. The [quickstart](docs/guide/quickstart.md) prepares a checkout and runs the smoke families.

## Documentation

- [User guide](docs/guide/): setup, running and voting on a family, and authoring cases, families, and systems
- [Design](docs/design/): how systems, the arena, ranking, reports, the site, and each family work
- [`AGENTS.md`](AGENTS.md): the entry point for contributors, linking the [conventions](docs/conventions/)

## License

[MIT](LICENSE)
