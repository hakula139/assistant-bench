# Quickstart

Set up a checkout and run the two smoke families.

## Requirements

- [Nix](https://nixos.org/download) with flakes enabled, which provides the other tools, while uv downloads CPython.
- On NixOS, [nix-ld](https://github.com/nix-community/nix-ld) enabled through `programs.nix-ld.enable`, as [Development](../conventions/development.md#toolchain-and-pins) explains.
- A running Docker daemon for task families, whose trials each start a container. A case family's trials run on the host.
- An OpenAI-compatible endpoint and API key, for families that call models.

## Setup

```bash
nix develop                                     # Dev shell with uv, Node.js, and the hooks
uv sync                                         # Virtualenv with Harbor and the bench
pnpm install                                    # Site and vote page dependencies
cp .env.example .env                            # Then fill in the endpoint variables
```

If you use [direnv](https://direnv.net), run `direnv allow` once so `.envrc` loads the dev shell whenever you enter the repository.

[Running](running.md#the-model-endpoint) describes each variable in `.env`.

## The Smoke Families

`smoke` contains one Harbor task with tests and a reference solution, and `smoke-text` contains two short cases that every system answers:

```bash
# Check the reference solution and do-nothing agent, with no endpoint
uv run bench check smoke

# Generate both sides of a pair
uv run bench run smoke-text --system <first> --system <second>
```

Pick two different names from `systems` in [`families/smoke-text/family.toml`](../../families/smoke-text/family.toml). Each committed system resolves to a model ID from the nixos-config catalog, so on an endpoint that names models differently, add a `route` table to `systems.toml` as [Authoring](authoring.md#systems) describes and list it in the family.

The run makes one request per case and selected system, then collects the replies into `outputs/smoke-text.jsonl`. Repeating it sends nothing until a case or system is added, though it retries every trial that errored.

The smoke run validates generation and collection. Public voting uses reviewed publications in the online arena, as [Arena](../design/arena.md) describes. Open [the vote page](https://bench.hakula.xyz/vote/) to vote on an available family. Voting needs two systems with passing, different replies on the same case. Failed gates or identical replies can leave no eligible pair even after both systems run.
