"""Systems as committed at another Git revision, for comparing fingerprints."""

import subprocess
from pathlib import Path

from assistant_bench.catalog import NixosConfigExport, System, parse_systems
from assistant_bench.paths import REPO_ROOT


SYSTEMS_FILE = 'systems.toml'


def systems_at(revision: str) -> dict[str, System]:
    """Resolve the revision's `systems.toml` against the revision's own export.

    A revision without `systems.toml` has no systems.

    Raises
    ------
    subprocess.CalledProcessError
        Git cannot resolve the revision, or Nix cannot build its export.
    """
    commit = _output('git', 'rev-parse', '--verify', f'{revision}^{{commit}}')
    if not _output('git', 'ls-tree', '--name-only', commit, SYSTEMS_FILE):
        return {}
    export_path = _output(
        'nix',
        'build',
        '--no-link',
        '--print-out-paths',
        f'git+file:{REPO_ROOT}?rev={commit}#nixos-config',
    )
    export = NixosConfigExport.model_validate_json(Path(export_path).read_bytes())
    return parse_systems(export, _output('git', 'show', f'{commit}:{SYSTEMS_FILE}'))


def changed_fingerprints(base: dict[str, System], head: dict[str, System]) -> list[str]:
    """Return the names of systems in both whose fingerprints differ."""
    return [
        name
        for name, system in head.items()
        if name in base and base[name].fingerprint != system.fingerprint
    ]


def _output(*args: str) -> str:
    return subprocess.run(
        args, check=True, capture_output=True, text=True, cwd=REPO_ROOT
    ).stdout.strip()
