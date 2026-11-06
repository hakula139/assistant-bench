# Python

Conventions for Python code and tests that the tooling and the module map don't already state.

## Architecture

- The [module map](../design/architecture.md#module-map) gives each module's responsibility, and imports stay within the [import layers](../design/architecture.md#import-layers).
- For anything Harbor reads or writes, build on Harbor's own models so a schema change surfaces as a validation error. Keep agents on `BaseAgent`, `AgentContext`, and the logs directory, the parts of Harbor's API least likely to churn. `Terminus` relies on private Harbor surface: `Terminus2._init_llm` to inject the endpoint client, `_query_llm`'s tenacity retry to exclude `NativeCompletionError`, `_run_agent_loop`, `_run_subagent`, and `_summarize` to keep SDK items across summaries, the `_llm` and `_chat` attributes, and `LiteLLM._build_base_kwargs` and `_extract_usage_info` to read LiteLLM's original usage. `HostEnvironment` implements `BaseEnvironment`'s changing abstract interface. Keep both integrations covered by real Harbor trials when upgrading Harbor.
- Model each input file the bench owns with a pydantic model that sets `extra='forbid'`, so a misspelled key fails at load time. Models of the nixos-config export accept extra fields, since that schema belongs to nixos-config.
- Branch on a family's scorer with a `match` over its settings model that ends in `case _: assert_never(...)`, so mypy reports each `match` a new scorer misses.
- Reach the data directory only through `paths.data_dir()`.

## Errors

- Raise `messages.InputError` for input an author or operator can fix, naming the item and the accepted values. Every message the CLI prints is one or more sentences, each starting with a capital and ending with a period, with names, files, and commands in backticks, and `messages.py` formats lists of names and pydantic errors that way. Wrap a `ValidationError` from author input in an `InputError` worded by `error_lines`, while pydantic validators keep raising `ValueError`. Commands raise these errors without catching them, and the root command group prints each `InputError` or `D1Error` to stderr and exits with code 1, keeping `typer.BadParameter` for an argument that names no family, task, or system. Any other exception is a bug and ends with its traceback. To see the traceback behind a printed error, rerun the command with `PYTHONDEVMODE=1`.
- Let HTTP and Harbor errors propagate. Harbor records them on the trial, and its retry policy decides what reruns.

## Modules

- Import Harbor's same-named models through their modules, as `jobs.py` does with `task_config.TaskConfig` and `trial_config.TaskConfig`, so each name shows which schema it belongs to.
- Separate groups of items in a module with `# ── Section Name ──` banners using U+2500, as `catalog.py` and `jobs.py` do.

## Dependencies

- Code follows the strict typing policy in [Development](development.md#dev-shell). Fix a diagnostic at the first-party contract behind it.
- Pin Harbor to an exact release, since it ships daily dev builds. Give every other dependency a lower bound at the version first locked, and let `uv.lock` pin the rest.
- Prefer packages already in Harbor's tree.

The development-only package under `typings/` supplies partial contracts for dependencies without typing metadata, covering only what the bench uses. `tests/test_repository.py` runs `mypy.stubtest` over every stub against the installed versions, checking each declared signature while allowing undeclared APIs.
