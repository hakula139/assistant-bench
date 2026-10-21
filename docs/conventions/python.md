# Python

This page outlines conventions for Python code under `src/` and `tests/`. Because tooling enforces some style rules, it lists those checks first, followed by the conventions reviewers need to check.

## Enforced by Tooling

- In `pyproject.toml`, `[tool.ruff]` selects the `B`, `C4`, `E`, `F`, `I`, `SIM`, `UP`, and `W` rules, formats with single quotes at 88 columns, and treats `src` and `tests` as first-party for import sorting.
- `[tool.mypy]` runs mypy in strict mode over `src` and `tests`. Since Harbor ships annotations without a `py.typed` marker, an override tells mypy to follow them.
- [Development](development.md#pre-commit-hooks) lists the pre-commit hooks, while [Development](development.md#where-settings-live) shows where each tool's settings live.

## Architecture

- The [module map](../design/architecture.md#module-map) describes each module's responsibility. Put functions and types in the module that owns their concept, and keep imports within the [import layers](../design/architecture.md#import-layers).
- For anything Harbor reads or writes, build on Harbor's own models, such as `JobConfig`, `TaskConfig`, `TrialResult`, and `AgentContext`, so schema changes surface as validation errors. Keep agents on `BaseAgent`, `AgentContext`, and the logs directory, which are the parts of Harbor's API least likely to churn.
- Model each input file with a pydantic model that sets `extra='forbid'`, so misspelled keys fail at load time.
- Branch on a family's scorer with a `match` over its settings model, ending with `case _: assert_never(...)`, so mypy reports any new scorer a `match` misses.
- Read the data directory only through `paths.data_dir()`, and keep every path under it.

## Errors

- Raise `ValueError` for input the author can fix. Name the item and accepted values in the message, as in `system 'x': effort 'medium' is not one of low, high`. The CLI turns an invalid family into `typer.BadParameter`.
- Use `assert` only to narrow a type the code already guarantees, such as `self.options` after Harbor validates it.
- Let HTTP and Harbor errors propagate. Harbor records them on the trial, and its retry policy decides what to rerun.

## Modules

- Keep each file focused on one concern, and split it when it starts mixing several.
- Prefix module-private helpers with `_`, and put helper functions after their callers.
- Import Harbor's same-named models through their modules, as `jobs.py` does with `task_config.TaskConfig` and `trial_config.TaskConfig`, so each name indicates which schema it belongs to.

## Layout

- Separate distinct groups of items with `# ── Section Name ──` banners using U+2500, as `catalog.py` and `jobs.py` do. Tests group by class instead, as [Testing](testing.md#grouping-and-naming) describes.
- In function bodies, use blank lines to separate logical phases such as setup, validation, execution, and result.

## Strings

Write prose and documents that span several lines, such as a prompt or fixture, as an indented triple-quoted string stripped with `textwrap.dedent`. Keep `\n` escapes for separators and single trailing newlines.

## Dependencies

- Pin Harbor to an exact release because it ships daily dev builds. Give every other dependency a lower bound at the version first locked, and let `uv.lock` pin the rest.
- Prefer packages already in Harbor's tree, such as pydantic, httpx, Typer, and the `openai` SDK. Add a dependency only when a change first requires it.
