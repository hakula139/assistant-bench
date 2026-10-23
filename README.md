# assistant-bench

[![CI](https://github.com/hakula139/assistant-bench/actions/workflows/ci.yml/badge.svg)](https://github.com/hakula139/assistant-bench/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/hakula139/assistant-bench/graph/badge.svg)](https://codecov.io/gh/hakula139/assistant-bench)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![WakaTime coding time for assistant-bench](https://wakatime.com/badge/user/f4a35a1f-0e29-4093-a647-e66aad164737/project/cbd2e36f-3ab4-4fc6-b6b9-840bd73f7594.svg)

A benchmark of AI assistant systems on everyday work, run on [Harbor](https://github.com/harbor-framework/harbor), with leaderboards published at [bench.hakula.xyz](https://bench.hakula.xyz) as rounds close.

## Overview

A system combines a model with its effort, harness, and system prompt. Every system in a family receives the same instructions, and the family scores their outputs either by the owner's blind pairwise votes or by each Harbor task's tests. Every output records its tokens, cost, latency, and the fingerprint of the system configuration behind it.

Early development. The first families, `writing-zh` and `writing-en`, rank how systems write everyday prose in Chinese and in English, from technical documents and messages to blog posts, essays, reviews, and fiction.

## Usage

Requires Nix with flakes (and nix-ld on NixOS), a running Docker daemon, and an OpenAI-compatible endpoint, as the [quickstart](docs/guide/quickstart.md#requirements) lists. Its [setup](docs/guide/quickstart.md#setup) prepares a checkout, and its [smoke walk-through](docs/guide/quickstart.md#the-smoke-families) runs and votes on the smoke families.

## Documentation

- [User guide](docs/guide/): setup, running and voting on a family, and authoring cases, families, and systems
- [Design](docs/design/): how systems, the arena, ranking, reports, the site, and each family work
- [`AGENTS.md`](AGENTS.md): the entry point for contributors, linking the [conventions](docs/conventions/)

## License

[MIT](LICENSE)
