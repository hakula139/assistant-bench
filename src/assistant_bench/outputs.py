"""Output records of a family's trials, as ingest writes them to the data directory."""

from pathlib import Path

from pydantic import BaseModel

from assistant_bench.families import Gate
from assistant_bench.paths import data_dir, write_atomically


class Output(BaseModel):
    """One trial of a family, as the arena and the reports read it.

    `gate_failures` lists the gates a voted output fails, so an empty list admits
    it to votes and any failure resolves its pairs automatically. It is `None` for
    an errored trial and for a family whose scorer has no gates.
    """

    trial: str
    job: str
    case: str
    system: str
    fingerprint: str | None
    nixos_config_rev: str | None
    error: str | None
    text: str | None
    reward: float | None
    gate_failures: list[Gate] | None
    finish_reason: str | None
    input_tokens: int | None
    cached_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    cost_usd: float | None
    latency_sec: float | None
    response_model: str | None
    request_id: str | None


def outputs_file(family: str) -> Path:
    return data_dir() / 'outputs' / f'{family}.jsonl'


def read_outputs(family: str) -> list[Output]:
    """Return the family's outputs, or none before its first ingest."""
    path = outputs_file(family)
    if not path.exists():
        return []
    return [Output.model_validate_json(line) for line in path.read_text().splitlines()]


def write_outputs(family: str, outputs: list[Output]) -> None:
    write_atomically(
        outputs_file(family),
        ''.join(f'{output.model_dump_json()}\n' for output in outputs),
    )
