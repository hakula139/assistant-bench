"""Build a pipeline's steps in dependency order."""

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field

from .config import RECORD_FILES, Pipeline
from .graph import order
from .state import STATE_PATH, State, fingerprint
from .steps import load_function, resolve_inputs, run_step


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

    A step that is up to date is skipped, and a failed step stops the build, so no
    step after it runs.
    """
    report = BuildReport()
    state = State.load(pipeline.root)
    for name in order(pipeline, target):
        spec = pipeline.steps[name]
        function, source = load_function(pipeline.root, spec)
        inputs = resolve_inputs(pipeline.root, spec.inputs)
        current = fingerprint(pipeline.root, spec, inputs, source)
        if state.fresh(spec, current):
            report.skipped.append(name)
            log(f'up to date {name}')
            continue

        state.forget(name)
        run_step(pipeline.root, spec, function, inputs)
        state.record(spec, current)
        report.ran.append(name)
        log(f'ran {name}')
    return report


def clean(pipeline: Pipeline) -> list[str]:
    """Delete every declared output and the build records, returning the outputs."""
    removed: list[str] = []
    for spec in pipeline.steps.values():
        for output in spec.outputs:
            path = pipeline.root / output
            if path.is_file():
                path.unlink()
                removed.append(output)

    for record in RECORD_FILES:
        (pipeline.root / record).unlink(missing_ok=True)
    with suppress(OSError):
        (pipeline.root / STATE_PATH.parent).rmdir()
    return removed
