# Python

Conventions for Python code under `src/` and `tests/` that the tooling and the module map don't already state.

## Architecture

- The [module map](../design/architecture.md#module-map) gives each module's responsibility, and imports stay within the [import layers](../design/architecture.md#import-layers).
- For anything Harbor reads or writes, build on Harbor's own models, such as `JobConfig`, `TaskConfig`, `TrialResult`, `TrialLock`, and `AgentContext`, so a schema change surfaces as a validation error. Keep agents on `BaseAgent`, `AgentContext`, and the logs directory, the parts of Harbor's API least likely to churn. `Terminus` is the exception: it subclasses Harbor's `Terminus2` and overrides its private `_init_llm` to inject the endpoint client, and the Harbor Smoke trial guards the subclass against a Harbor upgrade that changes it. `HostEnvironment` also subclasses Harbor's `BaseEnvironment`, whose abstract members change between releases, and the in-process jobs in `tests/harnesses/test_host.py` guard it in the default suite on every Harbor bump.
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

- Pin Harbor to an exact release, since it ships daily dev builds. Give every other dependency a lower bound at the version first locked, and let `uv.lock` pin the rest.
- Prefer packages already in Harbor's tree, such as pydantic, httpx, Typer, and the `openai` SDK.
