import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import tomllib
from collections.abc import Callable
from pathlib import Path
from shlex import quote
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit

import pytest
import tomli_w
from typer.testing import CliRunner

from assistant_bench import jobs, manifest, task_assets
from assistant_bench.catalog import load_export, load_systems
from assistant_bench.cli import app
from assistant_bench.families import Case, case_task, load_cases, load_family
from assistant_bench.paths import REPO_ROOT


CODING = REPO_ROOT / 'families' / 'coding-repo'
CODING_TASKS = sorted(path.parent for path in (CODING / 'tasks').glob('*/task.toml'))
CODING_FIXTURES = REPO_ROOT / 'tests' / 'fixtures' / 'coding-repo'
CODING_BYPASSES = sorted(path.stem for path in CODING_FIXTURES.glob('*.py'))
SOLVE_TIMEOUT_SEC = 60
COMMITTED_SOURCES = {'synthetic', 'public'}

URL = re.compile(r'[a-z][a-z0-9+.-]*://[^\s"\']+', re.IGNORECASE)


def hosts(path: Path) -> set[str | None]:
    return {
        urlsplit(match.group()).hostname for match in URL.finditer(path.read_text())
    }


class TestHosts:
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
    def test_reads_the_url_authority(
        self, url: str, host: str | None, tmp_path: Path
    ) -> None:
        path = tmp_path / 'lockfile'
        path.write_text(url)

        assert hosts(path) == {host}


@pytest.mark.parametrize(
    'path',
    sorted((REPO_ROOT / 'families').glob('*/cases.toml')),
    ids=lambda path: path.parent.name,
)
def test_committed_cases_hold_only_synthetic_or_public_sources(path: Path) -> None:
    cases = [
        Case.model_validate(case) for case in tomllib.loads(path.read_text())['cases']
    ]

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
    sorted(path.parent.name for path in (REPO_ROOT / 'families').glob('*/cases.toml')),
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


@pytest.mark.parametrize('shared', task_assets.shared_files(REPO_ROOT))
def test_coding_tasks_copy_their_shared_files(shared: Path) -> None:
    canonical = (CODING / 'shared' / shared).read_bytes()

    for task in CODING_TASKS:
        copy = task / shared

        assert copy.read_bytes() == canonical, f'`{copy}` needs regeneration'


def test_coding_tasks_hold_no_symlinks() -> None:
    assert [path for path in (CODING / 'tasks').rglob('*') if path.is_symlink()] == []


@pytest.mark.parametrize('task', CODING_TASKS, ids=lambda task: task.name)
def test_coding_references_ship_their_changes_and_the_packages_they_change(
    task: Path,
) -> None:
    starter = task / 'environment' / 'app'
    solution = task / 'solution' / 'app'
    shipped = {
        path.relative_to(solution) for path in solution.rglob('*') if path.is_file()
    }

    changed = {
        path
        for path in shipped
        if not (starter / path).is_file()
        or (starter / path).read_bytes() != (solution / path).read_bytes()
    }
    packages = {
        path.parts[0]
        for path in changed
        if path.suffix == '.py' and len(path.parts) > 1 and path.parts[0] != 'tests'
    }
    typed = {
        path.relative_to(starter)
        for package in packages
        for path in (starter / package).rglob('*.py')
    }

    assert shipped == changed | typed


def test_coding_fixture_directories_match_tasks() -> None:
    assert {path.name for path in CODING_FIXTURES.iterdir() if path.is_dir()} <= {
        task.name for task in CODING_TASKS
    }


def solved_workspace(task: Path, tmp_path: Path, *, solved: bool) -> Path:
    """Copy a task's starter into `tmp_path`, with the reference applied if `solved`."""
    workspace = tmp_path / 'app'
    shutil.copytree(task / 'environment' / 'app', workspace)
    if solved:
        solve = (task / 'solution' / 'solve.sh').read_text()
        solve = solve.replace(
            '"${BASH_SOURCE[0]}"', quote(str(task / 'solution' / 'solve.sh'))
        ).replace(' /app\n', f' {quote(str(workspace))}\n')
        subprocess.run(
            ['bash', '-s'],
            input=solve,
            cwd=workspace,
            text=True,
            check=True,
            timeout=SOLVE_TIMEOUT_SEC,
        )
    return workspace


def run_verifier(task: Path, workspace: Path, logs: Path, tmp_path: Path) -> str:
    """Run a task's `test.sh` against `workspace`, logging to `logs`, for its output."""
    verifier = tmp_path / 'tests'
    shutil.copytree(task / 'tests', verifier)
    verify = verifier / 'verify.py'
    verify.write_text(
        verify.read_text().replace(
            "pathlib.Path('/app')", f'pathlib.Path({str(workspace)!r})'
        )
    )
    script = (
        (verifier / 'test.sh')
        .read_text()
        .replace('/tests/verify.py', quote(str(verify)))
        .replace('/logs/verifier', quote(str(logs)))
    )
    result = subprocess.run(
        ['bash', '-s'],
        input=script,
        cwd=workspace,
        env={
            'PATH': os.pathsep.join(
                [str(Path(sys.executable).parent), os.environ['PATH']]
            ),
            'TMPDIR': str(tmp_path),
        },
        capture_output=True,
        text=True,
        check=True,
        timeout=verifier_wait_sec(),
    )
    return result.stdout + result.stderr


@pytest.mark.parametrize(
    ('task', 'candidate'),
    [
        pytest.param(task, candidate, id=f'{task.name}-{candidate}')
        for task in CODING_TASKS
        for candidate in (
            'starter',
            'reference',
            *(
                path.stem
                for path in sorted((CODING_FIXTURES / task.name).glob('*.patch'))
            ),
        )
    ],
)
def test_coding_verifiers_reward_only_the_reference(
    task: Path, candidate: str, tmp_path: Path
) -> None:
    workspace = solved_workspace(task, tmp_path, solved=candidate != 'starter')
    if candidate not in {'starter', 'reference'}:
        patch = CODING_FIXTURES / task.name / f'{candidate}.patch'
        subprocess.run(['git', 'init', '--quiet'], cwd=workspace, check=True)
        subprocess.run(['git', 'apply', str(patch)], cwd=workspace, check=True)
    logs = tmp_path / 'logs'

    output = run_verifier(task, workspace, logs, tmp_path)

    accepted = candidate == 'reference'
    assert (logs / 'reward.txt').read_text() == f'{int(accepted)}\n', output
    verdict = 'PASS' if accepted else 'FAIL'
    assert f'{verdict} the hidden behavior tests pass' in output, output
    if not accepted:
        assert '\nFAIL: ' in output, output
        assert not re.search(
            r'\b(ImportError|ModuleNotFoundError|NameError|SyntaxError)\b', output
        ), output


@pytest.mark.parametrize(
    ('task', 'bypass'),
    [
        pytest.param(task, bypass, id=f'{task.name}-{bypass}')
        for task in CODING_TASKS
        for bypass in CODING_BYPASSES
    ],
)
def test_coding_verifiers_reject_bypasses_and_planted_rewards(
    task: Path, bypass: str, tmp_path: Path
) -> None:
    workspace = solved_workspace(task, tmp_path, solved=True)
    source = (CODING_FIXTURES / f'{bypass}.py').read_text()
    for package in workspace.glob('*/__init__.py'):
        if package.parent.name != 'tests':
            package.write_text(package.read_text() + source)
    logs = tmp_path / 'logs'
    (logs / 'reward.json').mkdir(parents=True)
    (logs / 'reward.json' / 'reward').write_text('1')
    planted = tmp_path / 'planted'
    planted.write_text('1')
    (logs / 'reward.txt').symlink_to(planted)

    output = run_verifier(task, workspace, logs, tmp_path)

    reward = logs / 'reward.txt'
    assert not reward.is_symlink() and reward.read_text() == '0\n', output
    assert not (logs / 'reward.json').exists(), output
    assert 'FAIL the hidden behavior tests pass' in output, output


def load_coding_verifier() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        'coding_verify', CODING / 'shared' / 'tests' / 'verify.py'
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verifier_wait_sec() -> int:
    """Return the longest a coding verifier runs: both timed runs, plus a minute."""
    timeout: int = load_coding_verifier().TIMEOUT_SEC
    return 2 * timeout + 60


@pytest.mark.parametrize(
    ('record', 'expected', 'reason'),
    [
        pytest.param(
            None,
            None,
            'The run left no record, as it exited early or ran past 300 s.',
            id='no-record',
        ),
        pytest.param(
            {'intact': False, 'passed': ['t.a'], 'successful': True},
            None,
            None,
            id='workspace-passes-unchecked-machinery',
        ),
        pytest.param(
            {'intact': True, 'passed': ['t.a'], 'successful': False},
            None,
            'A test failed.',
            id='workspace-failure',
        ),
        pytest.param(
            {'intact': True, 'passed': [], 'successful': True},
            None,
            'No test passed.',
            id='workspace-without-passes',
        ),
        pytest.param(
            {'intact': True, 'passed': ['t.a', 't.b'], 'successful': True},
            ['t.a', 't.b'],
            None,
            id='hidden-exact-match',
        ),
        pytest.param(
            {'intact': False, 'passed': ['t.a', 't.b'], 'successful': True},
            ['t.a', 't.b'],
            'The run changed the `unittest` machinery.',
            id='hidden-machinery-changed',
        ),
        pytest.param(
            {'intact': True, 'passed': ['t.a'], 'successful': True},
            ['t.a', 't.b'],
            'These tests did not pass: t.b',
            id='hidden-missing-test',
        ),
        pytest.param(
            {'intact': True, 'passed': ['t.a', 't.b', 't.c'], 'successful': True},
            ['t.a', 't.b'],
            'Tests beyond the hidden test methods passed.',
            id='hidden-extra-test',
        ),
        pytest.param(
            {'intact': True, 'passed': ['t.a', 't.b'], 'successful': False},
            ['t.a', 't.b'],
            'A class or module fixture failed.',
            id='hidden-fixture-failure',
        ),
    ],
)
def test_coding_verifier_judges_each_run_record(
    record: dict[str, Any] | None, expected: list[str] | None, reason: str | None
) -> None:
    verify = load_coding_verifier()

    judged = verify.judge(None if record is None else verify.Record(**record), expected)

    assert judged == reason


@pytest.mark.parametrize('task', CODING_TASKS, ids=lambda task: task.name)
def test_coding_tasks_state_the_verifier_limit(task: Path) -> None:
    config = tomllib.loads((task / 'task.toml').read_text())
    pytest_config = tomllib.loads((REPO_ROOT / 'pyproject.toml').read_text())['tool'][
        'pytest'
    ]['ini_options']

    timeout = load_coding_verifier().TIMEOUT_SEC

    assert (timeout, config['environment']['cpus']) == (5 * 60, 2)
    assert (
        'fails either run that takes more than five minutes on two CPUs.'
        in (task / 'instruction.md').read_text()
    )
    assert config['verifier']['timeout_sec'] >= verifier_wait_sec()
    assert (
        pytest_config['faulthandler_timeout'] > SOLVE_TIMEOUT_SEC + verifier_wait_sec()
    )


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


def test_the_fingerprint_manifest_matches_the_runnable_systems(
    stale_fixture: Callable[[Path, str], None],
) -> None:
    path = REPO_ROOT / manifest.MANIFEST_FILE

    current = manifest.render_manifest(
        manifest.current_fingerprints(load_systems(load_export()))
    )

    if not path.is_file() or path.read_text() != current:
        stale_fixture(path, current)


@pytest.mark.parametrize(
    ('family', 'report', 'expected'),
    [
        (
            {'systems': ['tabled']},
            {'results': {'reference': 'retired'}},
            set[tuple[str, str]](),
        ),
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
            *('--rootdir', str(directory), '--no-cov', '-n', '0'),
            *('-p', 'no:cacheprovider'),
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

        import litellm
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
