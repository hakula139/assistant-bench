import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import tomli_w
from typer.testing import CliRunner

from assistant_bench import jobs
from assistant_bench.cli import app
from assistant_bench.families import Case, case_task, load_cases, load_family
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


@pytest.mark.parametrize(
    'name',
    sorted(path.parent.name for path in (REPO_ROOT / 'families').glob('*/tasks')),
)
def test_committed_task_families_set_what_a_run_needs(name: str) -> None:
    family = load_family(name)

    tasks = [case_task(family, case.id) for case in load_cases(family)]

    assert tasks
    jobs.check_tasks(family, tasks)


@pytest.mark.parametrize(
    'shared', ['environment/Dockerfile', 'tests/test.sh', 'tests/verify_lib.py']
)
def test_coding_tasks_share_their_common_files(shared: str) -> None:
    tasks = sorted((REPO_ROOT / 'families' / 'coding-repo' / 'tasks').iterdir())

    contents = {(task / shared).read_bytes() for task in tasks}

    assert len(contents) == 1, f'`{shared}` differs across the coding tasks'


@pytest.mark.parametrize(
    ('regression_tests', 'passes'),
    [('missing', False), ('return-value', True), ('recency', True)],
    ids=['missing', 'return-value', 'recency'],
)
def test_lru_verifier_requires_regressions_with_different_private_storage(
    regression_tests: str, passes: bool, tmp_path: Path
) -> None:
    task = REPO_ROOT / 'families' / 'coding-repo' / 'tasks' / 'fix-lru-cache'
    workspace = tmp_path / 'app'
    shutil.copytree(task / 'environment' / 'app', workspace)
    visible_tests = workspace / 'tests' / 'test_lru.py'
    original_tests = visible_tests.read_text()
    env = {
        'PATH': os.environ['PATH'],
        'BENCH_APP_DIR': str(workspace),
        'TMPDIR': str(tmp_path),
    }
    solution = (
        (task / 'solution' / 'solve.sh')
        .read_text()
        .replace('cd /app', 'cd "$BENCH_APP_DIR"')
    )
    subprocess.run(['bash', '-s'], input=solution, env=env, text=True, check=True)
    implementation = workspace / 'cachelite' / 'lru.py'
    implementation.write_text(implementation.read_text().replace('_data', '_entries'))
    if regression_tests == 'missing':
        visible_tests.write_text(original_tests)
    elif regression_tests == 'recency':
        before, assertion, after = visible_tests.read_text().partition(
            '        self.assertEqual(cache.get("a", "missing"), 0)\n'
        )
        assert assertion
        recency = textwrap.indent(
            textwrap.dedent(
                """\
                cache.put("b", 1)
                cache.get("a")
                cache.put("c", 2)
                self.assertEqual(cache.keys(), ["a", "c"])
                """
            ),
            '        ',
        )
        visible_tests.write_text(before + recency + after)

    result = subprocess.run(
        [sys.executable, str(task / 'tests' / 'verify.py')],
        capture_output=True,
        env=env,
        text=True,
        timeout=60,
    )

    assert (result.returncode == 0) is passes, result.stdout + result.stderr
    assert 'PASS the hidden spec tests pass' in result.stdout
    assert 'AttributeError' not in result.stdout + result.stderr
    for bug in (
        'get treats a falsy value as missing',
        'put of a cached key evicts another when full',
    ):
        verdict = 'PASS' if passes else 'FAIL'
        assert f'{verdict} tests/test_lru.py fails when {bug}' in result.stdout


def family_systems(config: dict[str, Any]) -> set[str]:
    """Includes the reference even when `systems` omits it."""
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


def run_pytest(
    directory: Path, test: str, *args: str, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run `test` under the project's pytest config, with only PATH and `env` set."""
    path = directory / 'test_inner.py'
    path.write_text(textwrap.dedent(test))
    return subprocess.run(
        [
            *(sys.executable, '-m', 'pytest', '-c', str(REPO_ROOT / 'pyproject.toml')),
            *('--rootdir', str(directory), '--no-cov', '-p', 'no:cacheprovider'),
            *(*args, str(path)),
        ],
        capture_output=True,
        env={'PATH': os.environ['PATH'], **env},
        text=True,
    )


def test_a_failing_test_report_leaves_out_the_environment(tmp_path: Path) -> None:
    secret = 'sentinel-secret-value'

    report = run_pytest(
        tmp_path,
        """\
        import sys
        from pathlib import Path

        import pytest
        from harbor.models.job.config import JobConfig

        from assistant_bench import jobs


        def test_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
            harbor = tmp_path / 'harbor'
            harbor.write_text('#!/bin/sh\\nexit 3\\n')
            harbor.chmod(0o755)
            monkeypatch.setattr(sys, 'executable', str(tmp_path / 'python'))
            jobs.run_job(JobConfig())
        """,
        env={'BENCH_TEST_SECRET': secret},
    )
    assert 'CalledProcessError' in report.stdout, report.stdout

    assert secret not in report.stdout + report.stderr


def test_tests_never_see_a_dotenv_above_litellm(tmp_path: Path) -> None:
    (tmp_path / '.env').write_text('BENCH_TEST_SECRET=from-dotenv\n')
    litellm = tmp_path / 'site' / 'litellm'
    litellm.mkdir(parents=True)
    # Mirrors LiteLLM, which loads the first .env above its own install.
    (litellm / '__init__.py').write_text(
        textwrap.dedent(
            """\
            import os

            import dotenv

            if os.getenv('LITELLM_MODE', 'DEV') == 'DEV':
                dotenv.load_dotenv()
            """
        )
    )

    report = run_pytest(
        tmp_path,
        """\
        import os

        import litellm  # noqa: F401
        import pytest

        AT_IMPORT = os.environ.get('BENCH_TEST_SECRET')


        @pytest.fixture(scope='session')
        def at_session() -> str | None:
            return os.environ.get('BENCH_TEST_SECRET')


        @pytest.fixture(scope='module')
        def at_module() -> str | None:
            return os.environ.get('BENCH_TEST_SECRET')


        def test_sees_no_dotenv(at_session: str | None, at_module: str | None) -> None:
            assert (AT_IMPORT, at_session, at_module) == (None, None, None)
        """,
        *('-p', 'conftest'),
        env={'PYTHONPATH': f'{tmp_path / "site"}:{REPO_ROOT / "tests"}'},
    )

    assert report.returncode == 0, report.stdout
