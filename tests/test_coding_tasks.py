import os
import shutil
import subprocess
import sys
from pathlib import Path
from shlex import quote

import pytest

from assistant_bench.paths import REPO_ROOT


CODING = REPO_ROOT / 'families' / 'coding-repo'
CANDIDATES = [
    ('deliver-transactional-outbox', 'starter'),
    ('deliver-transactional-outbox', 'reference'),
    ('deliver-transactional-outbox', 'split-order-commit'),
    ('deliver-transactional-outbox', 'regenerated-payload'),
    ('migrate-customer-store', 'starter'),
    ('migrate-customer-store', 'reference'),
    ('migrate-customer-store', 'early-version'),
    ('publish-asset-graph', 'starter'),
    ('publish-asset-graph', 'reference'),
    ('publish-asset-graph', 'raw-asset-hash'),
    ('publish-asset-graph', 'early-publication'),
    ('publish-asset-graph', 'activate-before-write'),
    ('repair-singleflight-cache', 'starter'),
    ('repair-singleflight-cache', 'reference'),
    ('repair-singleflight-cache', 'cross-tenant'),
    ('repair-singleflight-cache', 'stale-generation'),
    ('resume-ledger-import', 'starter'),
    ('resume-ledger-import', 'reference'),
    ('resume-ledger-import', 'checkpoint-split'),
    ('stabilize-cursor-pagination', 'starter'),
    ('stabilize-cursor-pagination', 'reference'),
    ('stabilize-cursor-pagination', 'no-snapshot'),
]
FAULT_PACKAGES = {
    'checkpoint-split': 'ledgerload',
    'cross-tenant': 'tenantcache',
    'no-snapshot': 'eventfeed',
    'stale-generation': 'tenantcache',
}
COMBINED_CHECK_TASKS = {
    'repair-singleflight-cache',
    'resume-ledger-import',
    'stabilize-cursor-pagination',
}


@pytest.mark.parametrize(
    ('task_name', 'candidate'),
    CANDIDATES,
    ids=[f'{task}-{candidate}' for task, candidate in CANDIDATES],
)
def test_coding_verifier_rewards_only_valid_candidates(
    tmp_path: Path, task_name: str, candidate: str
) -> None:
    task = CODING / 'tasks' / task_name
    workspace = tmp_path / 'app'
    shutil.copytree(task / 'environment' / 'app', workspace)
    env = {
        'PATH': os.pathsep.join([str(Path(sys.executable).parent), os.environ['PATH']]),
        'BENCH_APP_DIR': str(workspace),
        'TMPDIR': str(tmp_path),
        'PYTHONDONTWRITEBYTECODE': '1',
    }
    if candidate != 'starter':
        subprocess.run(
            ['bash', str(task / 'solution' / 'solve.sh')],
            cwd=workspace,
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    if candidate not in {'starter', 'reference'}:
        shutil.copytree(
            task / 'tests' / 'broken' / candidate,
            workspace / FAULT_PACKAGES.get(candidate, ''),
            dirs_exist_ok=True,
        )
        if task_name not in COMBINED_CHECK_TASKS:
            shutil.copytree(
                task / 'environment' / 'app' / 'tests',
                workspace / 'tests',
                dirs_exist_ok=True,
            )

    protected = tmp_path / 'protected'
    shutil.copytree(task / 'tests', protected)
    shutil.copyfile(
        CODING / 'shared' / 'tests' / 'verify_lib.py', protected / 'verify_lib.py'
    )
    logs = tmp_path / 'logs'
    script = (
        (protected / 'test.sh')
        .read_text()
        .replace('/tests/verify.py', quote(str(protected / 'verify.py')))
        .replace('/logs/verifier', quote(str(logs)))
    )

    result = subprocess.run(
        ['bash', '-s'],
        input=script,
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    output = result.stdout + result.stderr

    accepted = candidate == 'reference'
    assert result.returncode == 0, output
    assert (logs / 'reward.txt').read_text().strip() == str(int(accepted)), output
    verdict = 'PASS' if accepted else 'FAIL'
    if task_name in COMBINED_CHECK_TASKS:
        assert f'{verdict} The visible and hidden behavior checks pass' in output
    else:
        assert 'PASS the workspace tests pass' in output
        assert f'{verdict} the hidden behavior tests pass' in output

    if not accepted:
        assert '\nFAIL:' in output
        assert 'AssertionError' in output
        if task_name != 'repair-singleflight-cache' or candidate not in {
            'starter',
            'cross-tenant',
        }:
            assert '\nERROR:' not in output
