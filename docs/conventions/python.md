# Python

Conventions for Python code and tests that the tooling and the module map don't already state.

## Architecture

- The [module map](../design/architecture.md#module-map) gives each module's responsibility, and imports stay within the [import layers](../design/architecture.md#import-layers).
- For anything Harbor reads or writes, build on Harbor's own models so a schema change surfaces as a validation error. Keep agents on `BaseAgent`, `AgentContext`, and the logs directory, the parts of Harbor's API least likely to churn. `Terminus` needs the private `Terminus2._init_llm` hook to inject the endpoint client, and `HostEnvironment` implements `BaseEnvironment`'s changing abstract interface. Keep both integrations covered by real Harbor trials when upgrading Harbor.
- Model each input file the bench owns, such as `systems.toml`, `family.toml`, and `cases.jsonl`, with a pydantic model that sets `extra='forbid'`, so a misspelled key fails at load time. Models of the nixos-config export accept extra fields, since that schema belongs to nixos-config.
- Branch on a family's scorer with a `match` over its settings model that ends in `case _: assert_never(...)`, so mypy reports each `match` a new scorer misses.
- Reach the data directory only through `paths.data_dir()`.

## Errors

- Raise `ValueError` for input an author can fix, naming the item and the accepted values, as in ``System `x`: effort `medium` is not one of `low`, `high`.`` Every message the CLI prints is one or more sentences, each starting with a capital and ending with a period, with names, files, and commands in backticks, and `messages.py` formats lists of names and pydantic errors that way. The CLI prints the `ValueError` of an invalid family, case, task, or system to stderr and exits with code 1, keeping `typer.BadParameter` for an argument that names no family or system.
- Let HTTP and Harbor errors propagate. Harbor records them on the trial, and its retry policy decides what reruns.

## Modules

- Import Harbor's same-named models through their modules, as `jobs.py` does with `task_config.TaskConfig` and `trial_config.TaskConfig`, so each name shows which schema it belongs to.
- Separate groups of items in a module with `# ── Section Name ──` banners using U+2500, as `catalog.py` and `jobs.py` do.

## Dependencies

- `uv run basedpyright` and the editor use the strict policy in `pyproject.toml`. The recommended VS Code extensions use the checker installed in the selected `.venv`, so `uv sync` supplies the same version as CI. Keep first-party source and tests in that policy, and fix the contract behind a diagnostic without suppressing it.
- Pin Harbor to an exact release, since it ships daily dev builds. Give every other dependency a lower bound at the version first locked, and let `uv.lock` pin the rest.
- Prefer packages already in Harbor's tree, such as pydantic, httpx, Typer, and the `openai` SDK.

The development-only package under `typings/` supplies missing dependency contracts. Its Zhon and threadpoolctl stubs cover their public exports, while its native partial statsmodels stubs describe the NumPy `Logit` interface the ranking uses. CI runs `mypy.stubtest` against the installed versions. The statsmodels check permits missing declarations outside that partial scope and checks every declared signature. Extend the contracts and their runtime checks together when consuming another interface.
