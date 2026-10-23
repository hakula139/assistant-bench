import json
import re
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import tomli_w
from typer.testing import CliRunner

from assistant_bench.cli import app
from assistant_bench.families import Case
from assistant_bench.paths import REPO_ROOT


COMMITTED_SOURCES = {'synthetic', 'public'}

URL = re.compile(r'[a-z][a-z0-9+.-]*://[^\s"\']+', re.IGNORECASE)


def hosts(path: Path) -> set[str | None]:
    return {urlsplit(url).hostname for url in URL.findall(path.read_text())}


@pytest.mark.parametrize(
    'path',
    sorted((REPO_ROOT / 'families').glob('*/cases.jsonl')),
    ids=lambda path: path.parent.name,
)
def test_committed_cases_hold_only_synthetic_or_public_sources(path: Path) -> None:
    cases = [Case.model_validate_json(line) for line in path.read_text().splitlines()]

    assert {case.source for case in cases} <= COMMITTED_SOURCES


@pytest.mark.parametrize(
    'path',
    sorted((REPO_ROOT / 'families').glob('*/tasks/*/task.toml')),
    ids=lambda path: f'{path.parent.parent.parent.name}/{path.parent.name}',
)
def test_committed_tasks_hold_only_synthetic_or_public_sources(path: Path) -> None:
    metadata = tomllib.loads(path.read_text()).get('metadata', {})

    assert metadata.get('source') in COMMITTED_SOURCES


@pytest.mark.parametrize(
    'name',
    sorted(path.parent.name for path in (REPO_ROOT / 'families').glob('*/cases.jsonl')),
)
def test_committed_case_families_pass_check(name: str) -> None:
    result = CliRunner().invoke(app, ['check', name])

    assert result.exit_code == 0, result.output


def family_systems(config: dict[str, Any]) -> set[str]:
    """Return every system that a family's config names."""
    systems = {str(system) for system in config.get('systems', [])}
    reference = config.get('reference')
    return systems | ({str(reference)} if reference else set())


def untabled(root: Path) -> set[tuple[str, str]]:
    """Return each family and system whose system lacks a `systems.toml` table."""
    tabled = set(tomllib.loads((root / 'systems.toml').read_text()))
    return {
        (path.parent.name, system)
        for path in (root / 'families').glob('*/family.toml')
        for system in family_systems(tomllib.loads(path.read_text()))
        if system not in tabled
    }


def test_committed_families_name_only_tabled_systems() -> None:
    assert untabled(REPO_ROOT) == set()


@pytest.mark.parametrize(
    ('family', 'report', 'expected'),
    [
        ({'systems': ['tabled']}, {'results': {'reference': 'retired'}}, set()),
        ({'systems': ['untabled']}, {}, {('f', 'untabled')}),
        ({'reference': 'untabled'}, {}, {('f', 'untabled')}),
    ],
)
def test_a_family_system_needs_a_systems_toml_table(
    family: dict[str, Any],
    report: dict[str, Any],
    expected: set[tuple[str, str]],
    tmp_path: Path,
) -> None:
    (tmp_path / 'systems.toml').write_text(tomli_w.dumps({'tabled': {}}))
    (tmp_path / 'families' / 'f').mkdir(parents=True)
    (tmp_path / 'families' / 'f' / 'family.toml').write_text(tomli_w.dumps(family))
    (tmp_path / 'reports' / 'f').mkdir(parents=True)
    (tmp_path / 'reports' / 'f' / 'round.json').write_text(json.dumps(report))

    assert untabled(tmp_path) == expected


def lockfiles() -> list[Path]:
    """Return every lockfile and requirements file in the repo and its families."""
    families = REPO_ROOT / 'families'
    return sorted(
        [REPO_ROOT / 'uv.lock', REPO_ROOT / 'pnpm-lock.yaml']
        + list(families.rglob('uv.lock'))
        + list(families.rglob('pnpm-lock.yaml'))
        + list(families.rglob('requirements*.txt'))
    )


def registry(name: str) -> set[str]:
    """Return the public registry that a lockfile or requirements file may name."""
    if name == 'pnpm-lock.yaml':
        return {'registry.npmjs.org'}
    return {'pypi.org', 'files.pythonhosted.org'}


@pytest.mark.parametrize(
    ('url', 'host'),
    [
        ('https://internal.corp?@pypi.org/x', 'internal.corp'),
        ('https://internal.corp#@pypi.org/x', 'internal.corp'),
        ('HTTPS://internal.corp/x', 'internal.corp'),
        ('Git+SSH://git@internal.corp/x', 'internal.corp'),
        ('https://internal.corp:8080/x', 'internal.corp'),
        ('https://user:pass@internal.corp/x', 'internal.corp'),
        ('file:///etc/passwd', None),
    ],
)
def test_hosts_reads_the_url_authority(
    url: str, host: str | None, tmp_path: Path
) -> None:
    path = tmp_path / 'lockfile'
    path.write_text(url)

    assert hosts(path) == {host}


@pytest.mark.parametrize(
    'lockfile',
    lockfiles(),
    ids=lambda path: str(path.relative_to(REPO_ROOT)),
)
def test_lockfiles_name_only_their_public_registry(lockfile: Path) -> None:
    assert hosts(lockfile) <= registry(lockfile.name)
