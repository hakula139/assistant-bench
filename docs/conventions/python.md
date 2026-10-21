# Python

Conventions for Python code under `src/` and `tests/` that the tooling and the module map don't already state.

## Enforced by Tooling

- `pyproject.toml`'s `[tool.ruff]` selects the `B`, `C4`, `D`, `E`, `F`, `I`, `SIM`, `UP`, and `W` rules, formats with single quotes at 88 columns, and treats `src` and `tests` as first-party for import sorting.
- Docstrings follow the NumPy convention, which Ruff checks: an imperative summary line, then `Parameters`, `Returns`, or `Raises` sections only where the contract needs them.
- `[tool.mypy]` runs mypy in strict mode over `src` and `tests`. Since Harbor ships annotations without a `py.typed` marker, an override tells mypy to follow them.

## Architecture

- The [module map](../design/architecture.md#module-map) gives each module's responsibility, and imports stay within the [import layers](../design/architecture.md#import-layers).
- For anything Harbor reads or writes, build on Harbor's own models, such as `JobConfig`, `TaskConfig`, `TrialResult`, `TrialLock`, and `AgentContext`, so a schema change surfaces as a validation error. Keep agents on `BaseAgent`, `AgentContext`, and the logs directory, the parts of Harbor's API least likely to churn.
- Model each input file the bench owns, such as `systems.toml`, `family.toml`, and `cases.jsonl`, with a pydantic model that sets `extra='forbid'`, so a misspelled key fails at load time. Models of the nixos-config export accept extra fields, since that schema belongs to nixos-config.
- Branch on a family's scorer with a `match` over its settings model that ends in `case _: assert_never(...)`, so mypy reports each `match` a new scorer misses.
- Reach the data directory only through `paths.data_dir()`.

## Errors

- Raise `ValueError` for input an author can fix, naming the item and the accepted values, as in `system 'x': effort 'medium' is not one of low, high`. The CLI turns the `ValueError` of an invalid family or case into `typer.BadParameter`.
- Let HTTP and Harbor errors propagate. Harbor records them on the trial, and its retry policy decides what reruns.

## Modules

- Import Harbor's same-named models through their modules, as `jobs.py` does with `task_config.TaskConfig` and `trial_config.TaskConfig`, so each name shows which schema it belongs to.
- Separate groups of items in a module with `# ── Section Name ──` banners using U+2500, as `catalog.py` and `jobs.py` do.

## Dependencies

- Pin Harbor to an exact release, since it ships daily dev builds. Give every other dependency a lower bound at the version first locked, and let `uv.lock` pin the rest.
- Prefer packages already in Harbor's tree, such as pydantic, httpx, Typer, and the `openai` SDK.
