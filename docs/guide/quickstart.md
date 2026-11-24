# Quickstart

Set up a checkout and run the two smoke families.

## Requirements

- [Nix](https://nixos.org/download) with flakes enabled, which provides the other tools, while uv downloads CPython.
- On NixOS, [nix-ld](https://github.com/nix-community/nix-ld) enabled through `programs.nix-ld.enable`, as [Development](../conventions/development.md#toolchain-and-pins) explains.
- A running Docker daemon for task families, whose trials each start a container. A case family's trials run on the host.
- A model endpoint and API key for families that call models, as [Running](running.md#the-model-endpoint) describes.
- An authenticated private data repository and private D1 credentials for generation and collection, as [Data](../conventions/data.md#the-data-directory) and [Running](running.md#voting) describe. Public checks need neither.

## Setup

Run setup in each new checkout or worktree:

```bash
nix develop                                     # Dev shell with uv, Node.js, and the hooks
uv sync                                         # Virtualenv with Harbor and the bench
pnpm install                                    # Site and vote page dependencies
cp .env.example .env                            # Then fill in the endpoint variables
```

If you use [direnv](https://direnv.net), run `direnv allow` once so `.envrc` loads the dev shell whenever you enter the repository.

### Site Verification

`pnpm test` installs the matching Chromium headless shell before running the site and voting Worker tests, reusing the browser cache on later runs.

## The Smoke Families

`smoke` contains one Harbor task with tests and a reference solution, and `smoke-text` contains two short cases that every system answers:

```bash
# Check the reference solution and do-nothing agent, with no endpoint
uv run bench check smoke

# List the runnable systems and generate both sides of a pair
uv run bench systems smoke-text
uv run bench run --system smoke-text/opus-5.5-high --system smoke-text/gpt-6.1-sol-high
```

Pick any two IDs from `bench systems smoke-text` for the generation command. The endpoint must serve both models' routes from the nixos-config catalog.

The run makes one request per case and selected system, saves trial artifacts in the private repository, and seals a collected output generation in private D1. Repeating it sends nothing until a case or system is added, and reruns trials with confirmed terminal failures. An uncertain admitted request keeps its trial blocked until inspected.

The smoke run validates generation and collection. Public voting uses reviewed publications in the online arena, as [Arena](../design/arena.md) describes. Follow the site's Vote link to vote on an available family. Voting needs two systems with passing, different replies on the same case. Failed gates or identical replies can leave no eligible pair even after both systems run.
