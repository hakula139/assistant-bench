import re
from pathlib import Path

import pytest

from assistant_bench.families import Case, load_cases, load_family
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
    sorted(path.parent.name for path in (REPO_ROOT / 'families').glob('*/family.toml')),
)
def test_committed_families_load(name: str) -> None:
    family = load_family(name)

    if family.source == 'cases':
        assert load_cases(family)


def test_uv_lock_names_only_pypi() -> None:
    assert hosts(REPO_ROOT / 'uv.lock') <= {'pypi.org', 'files.pythonhosted.org'}


def test_pnpm_lock_names_only_the_npm_registry() -> None:
    assert hosts(REPO_ROOT / 'pnpm-lock.yaml') <= {'registry.npmjs.org'}
