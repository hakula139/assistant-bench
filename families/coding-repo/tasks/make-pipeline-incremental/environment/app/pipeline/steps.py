"""Resolve and call the functions that implement steps."""

import importlib
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
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


def load_function(root: Path, spec: StepSpec) -> StepFunction:
    """Import a step's `module:function` with the project root on the path."""
    module_name, _, function_name = spec.run.partition(':')
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    try:
        module = importlib.import_module(module_name)
    except ImportError as error:
        raise StepFailed(spec.name, f'cannot import {module_name}: {error}') from error

    function = getattr(module, function_name, None)
    if not callable(function):
        raise StepFailed(spec.name, f'{spec.run} is not a function.')
    return function


def resolve_inputs(root: Path, patterns: tuple[str, ...]) -> list[Path]:
    found: set[Path] = set()
    for pattern in patterns:
        found.update(path for path in root.glob(pattern) if path.is_file())
    return sorted(found)


def run_step(root: Path, spec: StepSpec, function: StepFunction) -> None:
    context = StepContext(
        root=root,
        inputs=resolve_inputs(root, spec.inputs),
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
