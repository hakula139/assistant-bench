"""Systems as committed at another Git revision, for comparing fingerprints."""

import subprocess
from pathlib import Path

from assistant_bench.catalog import (
    SYSTEMS_FILE,
    NixosConfigExport,
    System,
    parse_systems,
)
from assistant_bench.paths import REPO_ROOT


def systems_at(revision: str) -> dict[str, System]:
    """Resolve the revision's `systems.toml` against the revision's own export.

    Raises
    ------
    subprocess.CalledProcessError
        Git cannot resolve the revision or its `systems.toml`, or Nix cannot
        build its export.
    """
    commit = _output('git', 'rev-parse', '--verify', f'{revision}^{{commit}}')
    systems = _output('git', 'show', f'{commit}:{SYSTEMS_FILE}')

    export_path = _output(
        'nix',
        'build',
        '--no-link',
        '--print-out-paths',
        f'git+file:{REPO_ROOT}?rev={commit}#nixos-config',
    )

    export = NixosConfigExport.model_validate_json(Path(export_path).read_bytes())

    return parse_systems(export, systems)


def changed_fingerprints(base: dict[str, System], head: dict[str, System]) -> list[str]:
    """Return the names of systems in both whose fingerprints differ."""
    return [
        name
        for name, system in head.items()
        if name in base and base[name].fingerprint != system.fingerprint
    ]


def _output(*args: str) -> str:
    return subprocess.run(
        args, check=True, stdout=subprocess.PIPE, text=True, cwd=REPO_ROOT
    ).stdout.strip()
