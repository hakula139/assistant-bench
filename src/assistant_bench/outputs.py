"""Output records of a family's trials, collected into the data directory."""

from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from assistant_bench.families import Gate
from assistant_bench.paths import data_dir, write_atomically


class StopDetails(BaseModel):
    """The Messages API's account of a refusal."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    type: Literal['refusal']
    category: str | None
    """The policy area, from an open set such as `cyber`, `bio`, and `general_harms`."""

    explanation: str | None
    """Human-readable text that the API does not keep stable."""


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
    """For a single-call reply, or the completion failure that ended an agent run."""

    stop_details: StopDetails | None
    """Set only when a Messages refusal ended the trial, in a reply or an agent run."""

    gate_failures: list[Gate] | None
    """An empty list admits votes, while failures settle pairs automatically.

    Absent for scorers without gates or outputs without a verdict. Rendered cases
    lack a verdict after a trial error, and tasks lack one without a test reward.
    """

    reward: float | None

    input_tokens: int | None
    cached_tokens: int | None
    cache_write_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    cost_usd: float | None
    latency_sec: float | None

    response_model: str | None
    request_id: str | None
    nixos_config_rev: str | None
    harness_version: str | None
    """Harbor version that wrote a task's solution, absent for single-call agents."""


def tests_pass(reward: float | None) -> bool:
    """Whether a task's tests passed, which needs a reward of 1.

    An errored agent can still pass, as on a timeout, since Harbor runs the
    verifier, while a trial whose tests never ran has no reward and fails.
    """
    return reward is not None and reward >= 1


def system_fingerprints(outputs: Iterable[Output]) -> dict[str, str | None]:
    """Map each system with outputs to the one fingerprint they all carry.

    Raises
    ------
    ValueError
        A system has outputs under several fingerprints.
    """
    by_system: defaultdict[str, set[str | None]] = defaultdict(set)
    for output in outputs:
        by_system[output.system].add(output.fingerprint)

    for system, fingerprints in sorted(by_system.items()):
        if len(fingerprints) > 1:
            raise ValueError(
                f'System `{system}` has outputs under several fingerprints. Give '
                'the changed system a new name, as `docs/guide/authoring.md#systems` '
                'describes.'
            )

    return {system: fingerprints.pop() for system, fingerprints in by_system.items()}


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
