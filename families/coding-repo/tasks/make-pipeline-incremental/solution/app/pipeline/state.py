"""Records of successful step runs, which decide the steps a build can skip."""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self, cast

from .config import StepSpec


STATE_PATH = Path('.pipeline') / 'state.json'
STATE_VERSION = 1


def digest(path: Path) -> str:
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def fingerprint(root: Path, spec: StepSpec, inputs: list[Path], source: bytes) -> str:
    """Identify everything a step's outputs can depend on.

    That is its declaration, the source of its function's module, and the paths
    and contents of the input files it reads.
    """
    document = {
        'run': spec.run,
        'params': spec.params,
        'outputs': spec.outputs,
        'source': hashlib.sha256(source).hexdigest(),
        'inputs': [
            [path.relative_to(root).as_posix(), digest(path)] for path in inputs
        ],
    }
    encoded = json.dumps(document, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass
class State:
    root: Path
    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Each step's fingerprint and output digests from its last successful run."""

    @classmethod
    def load(cls, root: Path) -> Self:
        """Read the records, starting afresh when they are missing or unreadable."""
        try:
            document = json.loads((root / STATE_PATH).read_text())
        except FileNotFoundError, json.JSONDecodeError:
            return cls(root)

        match document:
            case {'version': version, 'steps': dict() as steps} if (
                version == STATE_VERSION
            ):
                return cls(root, cast(dict[str, dict[str, Any]], steps))
            case _:
                return cls(root)

    def fresh(self, spec: StepSpec, current: str) -> bool:
        """Whether a step last succeeded as `current` and its outputs are unchanged."""
        record = self.records.get(spec.name)
        if record is None or record.get('fingerprint') != current:
            return False

        outputs: dict[str, str] = record.get('outputs', {})
        for output in spec.outputs:
            path = self.root / output
            if not path.is_file() or outputs.get(output) != digest(path):
                return False
        return True

    def forget(self, name: str) -> None:
        if self.records.pop(name, None) is not None:
            self.save()

    def record(self, spec: StepSpec, current: str) -> None:
        self.records[spec.name] = {
            'fingerprint': current,
            'outputs': {output: digest(self.root / output) for output in spec.outputs},
        }
        self.save()

    def save(self) -> None:
        """Replace the records atomically, so an interrupted save keeps the old ones."""
        path = self.root / STATE_PATH
        path.parent.mkdir(exist_ok=True)
        temporary = path.with_name(f'{path.name}.tmp')
        temporary.write_text(
            json.dumps(
                {'version': STATE_VERSION, 'steps': self.records}, sort_keys=True
            )
        )
        temporary.replace(path)
