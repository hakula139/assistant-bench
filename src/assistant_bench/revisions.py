"""Systems as committed at another Git revision, for comparing fingerprints."""

import subprocess
import tomllib
from collections.abc import Mapping
from pathlib import Path

from assistant_bench.catalog import (
    SYSTEMS_FILE,
    NixosConfigExport,
    System,
    SystemIdentity,
    parse_system_configs,
    resolve_identity,
)
from assistant_bench.families import family_names, load_family
from assistant_bench.generation import parse_generation
from assistant_bench.harnesses import fingerprint
from assistant_bench.jobs import family_systems, listed_systems
from assistant_bench.messages import code
from assistant_bench.paths import REPO_ROOT


def current_systems(systems: dict[str, System]) -> dict[str, System]:
    """Capture each currently runnable family and system's generation identity."""
    resolved: dict[str, System] = {}
    for name in family_names():
        family = load_family(name)
        listed = listed_systems(family, systems)
        for system in family_systems(family, listed):
            resolved[f'{name}/{system.name}'] = system
    return resolved


def systems_at(revision: str, selected: set[str]) -> dict[str, SystemIdentity]:
    """Resolve the revision's `systems.toml` against the revision's own export.

    Raises
    ------
    subprocess.CalledProcessError
        Git cannot resolve the revision or its `systems.toml`, or Nix cannot
        build its export.
    """
    commit = _output('git', 'rev-parse', '--verify', f'{revision}^{{commit}}')
    selections: dict[str, tuple[str, list[str]]] = {}
    paths = _output('git', 'ls-tree', '-r', '--name-only', commit, 'families')
    for path in paths.splitlines():
        if not path.endswith('/family.toml'):
            continue
        family = Path(path).parent.name
        settings = tomllib.loads(_output('git', 'show', f'{commit}:{path}'))
        names = [
            name
            for name in settings.get('systems', [])
            if f'{family}/{name}' in selected
        ]
        if names:
            selections[family] = (settings.get('harness', 'generate'), names)
    if not selections:
        return {}

    try:
        generation = parse_generation(
            _output('git', 'show', f'{commit}:pyproject.toml'),
            _output('git', 'show', f'{commit}:uv.lock'),
        )
    except KeyError as error:
        raise ValueError(
            f'Revision `{revision}` lacks `{error.args[0]}` in its generation '
            'metadata for its shared runnable systems. Give those systems new names.'
        ) from error

    systems = _output('git', 'show', f'{commit}:{SYSTEMS_FILE}')

    export_path = _output(
        'nix',
        'build',
        '--no-link',
        '--print-out-paths',
        f'git+file:{REPO_ROOT}?rev={commit}#nixos-config',
    )

    export = NixosConfigExport.model_validate_json(Path(export_path).read_bytes())

    configs = parse_system_configs(systems)
    missing = {
        name for _, names in selections.values() for name in names
    } - configs.keys()
    if missing:
        raise ValueError(
            f'Revision `{revision}` does not define systems {code(sorted(missing))}.'
        )
    return {
        f'{family}/{name}': resolve_identity(
            name, configs[name], export, generation, harness
        )
        for family, (harness, names) in selections.items()
        for name in names
    }


def changed_fingerprints(
    base: Mapping[str, SystemIdentity], head: Mapping[str, SystemIdentity]
) -> list[str]:
    """Return the names of systems in both whose fingerprints differ."""
    return [
        name
        for name, system in head.items()
        if name in base and fingerprint(base[name]) != fingerprint(system)
    ]


def _output(*args: str) -> str:
    return subprocess.run(
        args, check=True, stdout=subprocess.PIPE, text=True, cwd=REPO_ROOT
    ).stdout.strip()
