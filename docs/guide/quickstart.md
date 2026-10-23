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
uv run bench check smoke                    # Run Harbor's oracle on the smoke task, which needs no endpoint
uv run bench run smoke-text                 # Send each case to every listed system, once each
uv run bench run --system <name>            # Send one system's missing trials to every family that lists it
uv run bench serve                          # Vote on the outputs the runs collected
```

`bench run smoke-text` makes one request per case and system, verifying that every system answers and reports usage, then collects the replies into `outputs/smoke-text.jsonl`. A second `bench run smoke-text` sends nothing until a case or system is added, though it retries every trial that errored.
