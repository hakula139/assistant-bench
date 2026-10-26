"""Build a pipeline's steps in dependency order."""

from collections.abc import Callable
from dataclasses import dataclass, field

from .config import Pipeline
from .graph import order
from .steps import load_function, run_step


@dataclass
class BuildReport:
    ran: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def build(
    pipeline: Pipeline,
    target: str | None = None,
    *,
    log: Callable[[str], object] = print,
) -> BuildReport:
    """Run the steps `target` needs, or every step, raising `StepFailed` on failure.

    A failed step stops the build, so no step after it runs.
    """
    report = BuildReport()
    for name in order(pipeline, target):
        spec = pipeline.steps[name]
        run_step(pipeline.root, spec, load_function(pipeline.root, spec))
        report.ran.append(name)
        log(f'ran {name}')
    return report


def clean(pipeline: Pipeline) -> list[str]:
    """Delete every declared output and return the ones that existed."""
    removed: list[str] = []
    for spec in pipeline.steps.values():
        for output in spec.outputs:
            path = pipeline.root / output
            if path.is_file():
                path.unlink()
                removed.append(output)
    return removed
