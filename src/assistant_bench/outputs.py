"""Output records of a family's trials, collected into the data directory."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from assistant_bench.families import Gate
from assistant_bench.paths import data_dir, write_atomically


class Output(BaseModel):
    """One trial of a family, as the arena and the reports read it."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    case: str
    system: str
    fingerprint: str | None
    job: str
    trial: str

    error: str | None
    text: str | None
    """For voted tasks, the diff of the solution the tests graded."""

    finish_reason: str | None
    gate_failures: list[Gate] | None
    """An empty list admits votes, while failures settle pairs automatically.

    Absent for scorers without gates or outputs without a verdict. Rendered cases
    lack a verdict after a trial error, and tasks lack one without a test reward.
    """

    reward: float | None

    input_tokens: int | None
    cached_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    cost_usd: float | None
    latency_sec: float | None

    response_model: str | None
    request_id: str | None
    nixos_config_rev: str | None
    harness_version: str | None
    """Harbor version that wrote a task's solution, absent for single-call agents."""


def read_outputs(family: str) -> list[Output]:
    """Return the family's outputs, or none before its first collection."""
    path = _outputs_file(family)
    if not path.exists():
        return []
    return [Output.model_validate_json(line) for line in path.read_text().splitlines()]


def write_outputs(family: str, outputs: list[Output]) -> None:
    write_atomically(
        _outputs_file(family),
        ''.join(f'{output.model_dump_json()}\n' for output in outputs),
    )


def _outputs_file(family: str) -> Path:
    return data_dir() / 'outputs' / f'{family}.jsonl'
