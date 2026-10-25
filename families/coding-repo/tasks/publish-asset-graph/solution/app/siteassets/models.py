"""Published build contracts."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BuildResult:
    directory: Path
    manifest: dict[str, str]


class BuildError(ValueError):
    pass
