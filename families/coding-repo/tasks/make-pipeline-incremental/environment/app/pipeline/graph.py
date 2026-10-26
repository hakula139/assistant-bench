"""Order a pipeline's steps by the files they exchange."""

from graphlib import CycleError, TopologicalSorter
from pathlib import PurePosixPath

from .config import ConfigError, Pipeline, StepSpec


def dependencies(pipeline: Pipeline) -> dict[str, set[str]]:
    """Map each step to the steps that write a file one of its inputs matches."""
    owners = {
        output: step.name for step in pipeline.steps.values() for output in step.outputs
    }
    return {
        step.name: {
            owner
            for output, owner in owners.items()
            if owner != step.name and _consumes(step, output)
        }
        for step in pipeline.steps.values()
    }


def order(pipeline: Pipeline, target: str | None = None) -> list[str]:
    """Return the steps to build, each after the steps it depends on.

    With a `target`, return only that step and its transitive dependencies.
    """
    graph = dependencies(pipeline)
    if target is not None:
        if target not in graph:
            raise ConfigError(f'No step is named {target!r}.')
        graph = {name: graph[name] for name in _closure(graph, target)}

    try:
        return list(TopologicalSorter(graph).static_order())
    except CycleError as error:
        raise ConfigError(
            f'Steps depend on each other in a cycle: {error.args[1]}.'
        ) from None


def _closure(graph: dict[str, set[str]], target: str) -> set[str]:
    selected: set[str] = set()
    pending = [target]
    while pending:
        name = pending.pop()
        if name not in selected:
            selected.add(name)
            pending.extend(graph[name])
    return selected


def _consumes(step: StepSpec, output: str) -> bool:
    path = PurePosixPath(output)
    return any(path.full_match(pattern) for pattern in step.inputs)
