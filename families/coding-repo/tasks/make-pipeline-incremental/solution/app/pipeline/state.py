"""Records of successful step runs, which decide the steps a build can skip."""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

from .config import RECORD_FILES, StepSpec


STATE_PATH, TEMPORARY_PATH = (Path(path) for path in RECORD_FILES)
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
        'patterns': spec.inputs,
        # A repr keeps a TOML date apart from the string it formats as.
        'params': repr(spec.params),
        'outputs': spec.outputs,
        'source': hashlib.sha256(source).hexdigest(),
        'inputs': [
            [path.relative_to(root).as_posix(), digest(path)] for path in inputs
        ],
    }
    encoded = json.dumps(document, sort_keys=True).encode()
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
            document: dict[str, Any] = json.loads((root / STATE_PATH).read_text())
        except FileNotFoundError, json.JSONDecodeError:
            return cls(root)

        if document.get('version') != STATE_VERSION:
            return cls(root)
        return cls(root, document['steps'])

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
        temporary = self.root / TEMPORARY_PATH
        temporary.write_text(
            json.dumps(
                {'version': STATE_VERSION, 'steps': self.records}, sort_keys=True
            )
        )
        temporary.replace(path)
