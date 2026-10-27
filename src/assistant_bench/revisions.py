"""The committed fingerprint manifest, for comparing fingerprints across revisions."""

import json
import subprocess
from collections.abc import Mapping

from pydantic import TypeAdapter

from assistant_bench.catalog import System
from assistant_bench.families import family_names, load_family
from assistant_bench.harnesses import fingerprint
from assistant_bench.jobs import family_systems, listed_systems
from assistant_bench.paths import REPO_ROOT
from assistant_bench.report import reported_systems


MANIFEST_FILE = 'fingerprints.json'

_MANIFEST = TypeAdapter(dict[str, str])


def current_systems(systems: dict[str, System]) -> dict[str, System]:
    """Map `<family>/<system>` to each runnable system as its family runs it."""
    resolved: dict[str, System] = {}
    for name in family_names():
        family = load_family(name)
        listed = listed_systems(family, systems)
        for system in family_systems(family, listed):
            resolved[f'{name}/{system.name}'] = system
    return resolved


def current_fingerprints(systems: dict[str, System]) -> dict[str, str]:
    """Map `<family>/<system>` to the fingerprint of each runnable system."""
    return {
        key: fingerprint(system)
        for key, system in sorted(current_systems(systems).items())
    }


def render_manifest(fingerprints: Mapping[str, str]) -> str:
    return f'{json.dumps(dict(sorted(fingerprints.items())), indent=2)}\n'


def manifest_at(revision: str) -> dict[str, str] | None:
    """Read the fingerprint manifest committed at the revision, if it has one.

    Raises
    ------
    subprocess.CalledProcessError
        Git cannot resolve the revision.
    ValueError
        The manifest does not map names to fingerprints.
    """
    commit = _output('git', 'rev-parse', '--verify', f'{revision}^{{commit}}')
    if not _output('git', 'ls-tree', '--name-only', commit, '--', MANIFEST_FILE):
        return None
    return _MANIFEST.validate_json(_output('git', 'show', f'{commit}:{MANIFEST_FILE}'))


def changed_fingerprints(base: Mapping[str, str], head: Mapping[str, str]) -> list[str]:
    """Return the `<family>/<system>` keys in both whose fingerprints differ."""
    return [key for key, value in head.items() if key in base and base[key] != value]


def reported(key: str) -> bool:
    """Return whether a closed report of the key's family names its system."""
    family, system = key.split('/')
    return system in reported_systems(family)


def _output(*args: str) -> str:
    return subprocess.run(
        args, check=True, stdout=subprocess.PIPE, text=True, cwd=REPO_ROOT
    ).stdout.strip()
