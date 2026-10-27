"""Resolve and call the functions that implement steps."""

import importlib.util
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from .config import StepSpec


@dataclass(frozen=True)
class StepContext:
    """What a step function receives."""

    root: Path
    inputs: list[Path]
    """Every existing file the step's input patterns match, sorted."""

    outputs: list[Path]
    """The step's outputs in declared order, whose parent directories exist."""

    params: dict[str, Any]


type StepFunction = Callable[[StepContext], object]


class StepFailed(Exception):
    def __init__(self, step: str, reason: str) -> None:
        self.step = step
        super().__init__(f'Step {step!r} failed: {reason}')


_sources: dict[str, bytes] = {}
"""The source each step module last ran from, by module name."""


def load_function(root: Path, spec: StepSpec) -> tuple[StepFunction, bytes]:
    """Import a step's `module:function` with the project root on the path.

    Return the function with the source its module ran from. A module whose file
    changed since it last ran in this process runs again from the current source,
    so the function always comes from the source returned with it.
    """
    module_name, _, function_name = spec.run.partition(':')
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    try:
        module, source = _run_module(module_name)
    except ImportError as error:
        raise StepFailed(spec.name, f'cannot import {module_name}: {error}') from error

    function = getattr(module, function_name, None)
    if not callable(function):
        raise StepFailed(spec.name, f'{spec.run} is not a function.')
    return function, source


def _run_module(name: str) -> tuple[ModuleType, bytes]:
    module_spec = importlib.util.find_spec(name)
    if module_spec is None or module_spec.origin is None:
        raise ModuleNotFoundError(f'No module named {name!r}', name=name)

    source = Path(module_spec.origin).read_bytes()
    module = sys.modules.get(name)
    if module is not None and _sources.get(name) == source:
        return module, source

    module = importlib.util.module_from_spec(module_spec)
    sys.modules[name] = module
    try:
        exec(compile(source, module_spec.origin, 'exec'), module.__dict__)
    except BaseException:
        del sys.modules[name]
        raise
    _sources[name] = source
    return module, source


def resolve_inputs(root: Path, patterns: tuple[str, ...]) -> list[Path]:
    found: set[Path] = set()
    for pattern in patterns:
        found.update(path for path in root.glob(pattern) if path.is_file())
    return sorted(found)


def run_step(
    root: Path, spec: StepSpec, function: StepFunction, inputs: list[Path]
) -> None:
    context = StepContext(
        root=root,
        inputs=inputs,
        outputs=[root / output for output in spec.outputs],
        params=dict(spec.params),
    )
    for output in context.outputs:
        output.parent.mkdir(parents=True, exist_ok=True)

    try:
        function(context)
    except Exception as error:
        raise StepFailed(spec.name, f'{type(error).__name__}: {error}') from error

    missing = [output for output in spec.outputs if not (root / output).is_file()]
    if missing:
        raise StepFailed(spec.name, f'it did not write {", ".join(missing)}.')
