"""Load and validate a project's `pipeline.toml`."""

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, cast


CONFIG_NAME = 'pipeline.toml'

_STEP_NAME = re.compile(r'[a-z][a-z0-9_-]*')
_CALLABLE = re.compile(r'[A-Za-z_][\w.]*:[A-Za-z_]\w*')


class ConfigError(Exception):
    """A `pipeline.toml` that cannot describe a pipeline."""


@dataclass(frozen=True)
class StepSpec:
    name: str
    run: str
    """The step function as `module:function`, imported from the project root."""

    inputs: tuple[str, ...]
    """Glob patterns relative to the project root."""

    outputs: tuple[str, ...]
    """Paths relative to the project root."""

    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Pipeline:
    root: Path
    steps: dict[str, StepSpec]


def load(root: Path) -> Pipeline:
    path = root / CONFIG_NAME
    try:
        document = tomllib.loads(path.read_text())
    except FileNotFoundError:
        raise ConfigError(f'{path} does not exist.') from None
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f'{path} is not valid TOML: {error}') from None

    tables = document.get('steps')
    if not isinstance(tables, dict) or not tables:
        raise ConfigError(f'{path} defines no [steps.NAME] tables.')

    steps = {
        name: _step(name, table)
        for name, table in cast(dict[str, object], tables).items()
    }
    owners: dict[str, str] = {}
    for step in steps.values():
        for output in step.outputs:
            if output in owners:
                raise ConfigError(
                    f'Steps {owners[output]!r} and {step.name!r} both write {output}.'
                )
            owners[output] = step.name
    return Pipeline(root, steps)


def _step(name: str, value: object) -> StepSpec:
    if not _STEP_NAME.fullmatch(name):
        raise ConfigError(f'Invalid step name {name!r}.')
    if not isinstance(value, dict):
        raise ConfigError(f'[steps.{name}] must be a table.')

    table = cast(dict[str, object], value)
    run = table.get('run')
    if not isinstance(run, str) or not _CALLABLE.fullmatch(run):
        raise ConfigError(f'[steps.{name}] run must name a module:function.')

    params = table.get('params', {})
    if not isinstance(params, dict):
        raise ConfigError(f'[steps.{name}] params must be a table.')

    outputs = _paths(name, 'outputs', table.get('outputs'))
    if not outputs:
        raise ConfigError(f'[steps.{name}] must declare at least one output.')
    return StepSpec(
        name=name,
        run=run,
        inputs=_paths(name, 'inputs', table.get('inputs', [])),
        outputs=outputs,
        params=cast(dict[str, Any], params),
    )


def _paths(name: str, key: str, value: object) -> tuple[str, ...]:
    items = cast(list[object], value) if isinstance(value, list) else None
    if items is None or not all(isinstance(item, str) for item in items):
        raise ConfigError(f'[steps.{name}] {key} must be a list of strings.')

    paths: list[str] = []
    for item in cast(list[str], items):
        path = PurePosixPath(item)
        if path.is_absolute() or '..' in path.parts or not path.parts:
            raise ConfigError(f'[steps.{name}] {key} must stay inside the project.')
        paths.append(path.as_posix())
    return tuple(paths)
