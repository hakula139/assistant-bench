# Development

## Dev Shell

Follow [Quickstart](../guide/quickstart.md#setup) for checkout and browser setup. The dev shell supplies uv, Node.js, pnpm and the nixos-config export. Editors use the root `.venv`.

Host code and authored task code share the strict typing policy in `pyproject.toml`. Missing library metadata is permitted, while missing imports and unknown or incompatible API types are errors. Starters resolve only their own modules, while task references and hidden tests resolve the reference package.

The voting Worker's disposable `local` environment uses Cloudflare's always-passing Turnstile test keys. Production, staging and private stores remain separate, as [Arena](../design/arena.md#vote-store) describes.

## Pre-Commit Hooks

`flake.nix` enables devkit's [pre-commit hooks](https://github.com/hakula139/devkit/blob/v3/docs/hooks.md). [AGENTS.md](../../AGENTS.md#verification) lists the remaining checks. Authored applications, references and hidden tests follow the host's formatting and spelling. Deliberately wrong regression patches remain unformatted and untyped so the real verifier tests their failure.

## Where Settings Live

Commands select what to act on. Systems and families own benchmark settings in `systems.toml` and `family.toml`, while `pyproject.toml` owns generation protocol versions. Model allowances come from the shared catalog. Tool policies belong in their tools' configuration files.

Environment variables carry credentials and per-environment values. Running covers the [endpoint](../guide/running.md#the-model-endpoint) and [cloud credentials](../guide/running.md#voting), [Data](data.md#the-data-directory) covers private sources and the disposable cache, and [CI](ci.md#deploy) covers deployment values. Generated Harbor job configs are runtime records, never committed configuration.

## Toolchain and Pins

uv installs the managed CPython selected by `.python-version`. Existing installations keep their patch release until `uv python upgrade`. On NixOS, enable `programs.nix-ld` with the C++ runtime and zlib needed by Linux wheels.

Review lock updates for their behavioral effects. A changed fingerprint needs the procedure in [Systems](../design/systems.md#fingerprints). A changed pnpm version also needs the GitLab Pages pin checked by `pages-pnpm`. Python and Node dependency versions are locked in `uv.lock` and `pnpm-lock.yaml`.

## The nixos-config Input

The flake reads the model catalog and the rendered assistant instructions from [nixos-config](https://github.com/hakula139/nixos-config), as [Systems](../design/systems.md#the-nixos-config-export) describes:

```bash
nix build .#nixos-config                    # Build the export on its own
nix flake update nixos-config               # Move to the latest nixos-config commit
```

A bump that changes a system's resolved prompt, route, or effort changes its fingerprint, and [Systems](../design/systems.md#fingerprints) states whether the system keeps its name. The rendered instructions cite devkit's source path, so a devkit bump changes the prompts of the `nixos-config:<client>` systems too.
