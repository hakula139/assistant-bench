"""The committed manifest of every runnable system's fingerprint."""

import json
from collections.abc import Mapping

from assistant_bench.catalog import System
from assistant_bench.families import family_names, load_family
from assistant_bench.harnesses import fingerprint
from assistant_bench.jobs import family_systems, listed_systems


MANIFEST_FILE = 'fingerprints.json'


def runnable_systems(systems: dict[str, System]) -> dict[str, list[System]]:
    """Map each family to its runnable systems as the family runs them."""
    runnable: dict[str, list[System]] = {}
    for name in family_names():
        family = load_family(name)
        runnable[name] = family_systems(family, listed_systems(family, systems))
    return runnable


def current_fingerprints(systems: dict[str, System]) -> dict[str, str]:
    """Map `<family>/<system>` to the fingerprint of each runnable system."""
    return {
        f'{family}/{system.name}': fingerprint(system)
        for family, members in runnable_systems(systems).items()
        for system in members
    }


def render_manifest(fingerprints: Mapping[str, str]) -> str:
    return f'{json.dumps(fingerprints, indent=2, sort_keys=True)}\n'
