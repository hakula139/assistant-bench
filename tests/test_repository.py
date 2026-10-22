import json
import re
import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from assistant_bench.cli import app
from assistant_bench.families import Case
from assistant_bench.paths import REPO_ROOT


COMMITTED_SOURCES = {'synthetic', 'public'}


def hosts(path: Path) -> set[str]:
    return set(re.findall(r'https?://([^/"\'\s]+)', path.read_text()))


@pytest.mark.parametrize(
    'path',
    sorted((REPO_ROOT / 'families').glob('*/cases.jsonl')),
    ids=lambda path: path.parent.name,
)
def test_committed_cases_hold_only_synthetic_or_public_sources(path: Path) -> None:
    cases = [Case.model_validate_json(line) for line in path.read_text().splitlines()]

    assert {case.source for case in cases} <= COMMITTED_SOURCES


@pytest.mark.parametrize(
    'name',
    sorted(path.parent.name for path in (REPO_ROOT / 'families').glob('*/cases.jsonl')),
)
def test_committed_case_families_pass_check(name: str) -> None:
    result = CliRunner().invoke(app, ['check', name])

    assert result.exit_code == 0, result.output


def named_systems(value: object) -> set[str]:
    """Collect every system a report names, in any results variant."""
    match value:
        case dict():
            names = {
                name
                for key, name in value.items()
                if key in {'system', 'opponent', 'leader', 'reference'}
            }
            names |= set(value.get('unranked', []))
            return names.union(*(named_systems(item) for item in value.values()))
        case list():
            return set().union(*(named_systems(item) for item in value))
        case _:
            return set()


def test_named_systems_reads_every_key_that_names_a_system() -> None:
    results = {
        'reference': 'reference',
        'standings': [{'system': 'ranked'}],
        'unranked': ['unranked'],
        'head_to_head': [{'system': 'ranked', 'opponent': 'opponent'}],
        'categories': [{'standings': [{'system': 'category'}]}],
        'decision': {'leader': 'leader'},
    }

    names = named_systems({'family': 'f', 'results': results})

    assert names == {
        'reference',
        'ranked',
        'unranked',
        'opponent',
        'category',
        'leader',
    }


def test_committed_families_and_reports_name_only_tabled_systems() -> None:
    tabled = set(tomllib.loads((REPO_ROOT / 'systems.toml').read_text()))
    named = {
        (path.parent.name, system)
        for path in (REPO_ROOT / 'families').glob('*/family.toml')
        for system in tomllib.loads(path.read_text()).get('systems', [])
    } | {
        (str(path.relative_to(REPO_ROOT)), system)
        for path in (REPO_ROOT / 'reports').glob('*/*.json')
        for system in named_systems(json.loads(path.read_text()))
    }

    assert {
        (source, system) for source, system in named if system not in tabled
    } == set()


def test_uv_lock_names_only_pypi() -> None:
    assert hosts(REPO_ROOT / 'uv.lock') <= {'pypi.org', 'files.pythonhosted.org'}


def test_pnpm_lock_names_only_the_npm_registry() -> None:
    assert hosts(REPO_ROOT / 'pnpm-lock.yaml') <= {'registry.npmjs.org'}
