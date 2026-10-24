"""Verify Harbor task bytes before separating agent inputs from grading inputs."""

from dataclasses import dataclass
from pathlib import Path

from harbor.models.task.config import TaskConfig
from harbor.publisher.packager import Packager
from pydantic import JsonValue

from assistant_bench_arena.serialization import digest


@dataclass(frozen=True)
class TaskProof:
    task_digest: str
    source_digest: str
    instruction: str


def prove_task(directory: Path, expected_digest: str) -> TaskProof:
    """Require the recorded full task hash before deriving a grading-stable key."""
    directory = directory.resolve()
    before, files = Packager.compute_content_hash(directory)
    if f'sha256:{before}' != expected_digest:
        raise ValueError('The task source does not match the recorded Harbor digest.')

    config = TaskConfig.model_validate_toml((directory / 'task.toml').read_text())
    if config.steps:
        raise ValueError('Multistep task source proof is not supported.')

    inputs = {directory / 'instruction.md'}
    environment = directory / 'environment'
    environment_entries = tuple(environment.rglob('*'))
    if environment.is_symlink() or any(
        path.is_symlink() for path in environment_entries
    ):
        raise ValueError('Native task hashes cannot prove environment symlink targets.')

    if environment.exists():
        inputs.update(path for path in environment_entries if path.is_file())

    if any(path.is_symlink() for path in inputs) or not inputs.issubset(files):
        raise ValueError('Every agent input must have native packaged byte provenance.')

    file_hashes: dict[str, JsonValue] = {
        path.relative_to(directory).as_posix(): Packager.compute_file_hash(path)
        for path in sorted(inputs)
    }
    source_digest = digest(
        {
            'schema': 'harbor-single-step-agent-input-v1',
            'files': file_hashes,
            'configuration': config.model_dump(
                mode='json', include={'schema_version', 'agent', 'environment'}
            ),
        }
    )
    instruction = (directory / 'instruction.md').read_text()
    after, _ = Packager.compute_content_hash(directory)
    if after != before:
        raise ValueError('The task source changed while its input proof was read.')

    return TaskProof(expected_digest, source_digest, instruction)
