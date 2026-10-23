# Quickstart

Set up a checkout and run the two smoke families.

## Requirements

- [Nix](https://nixos.org/download) with flakes enabled, which provides the other tools, while uv downloads CPython.
- On NixOS, [nix-ld](https://github.com/nix-community/nix-ld) enabled through `programs.nix-ld.enable`, as [Development](../conventions/development.md#toolchain-and-pins) explains.
- A running Docker daemon, since Harbor starts a container for every trial.
- An OpenAI-compatible endpoint and API key, for families that call models.

## Setup

```bash
nix develop                                 # Dev shell with uv, Node.js, and the hooks
uv sync                                     # Virtualenv with Harbor and the bench
cp .env.example .env                        # Then fill in the endpoint variables
```

[Running](running.md#the-model-endpoint) describes each variable in `.env`.

## The Smoke Families

`smoke` contains one Harbor task with tests and a reference solution, and `smoke-text` contains two short cases that every system answers:

```bash
uv run bench check smoke                        # Run the oracle and nop on the smoke task, which needs no endpoint
uv run bench run smoke-text --system <name>     # Send each smoke case to one listed system
uv run bench serve                              # Vote on the outputs the run collected
```

`bench run smoke-text --system <name>` makes one request per case for that system, verifying that it answers and reports usage, then collects the replies into `outputs/smoke-text.jsonl`. A second run sends nothing until a case or system is added, though it retries every trial that errored.
