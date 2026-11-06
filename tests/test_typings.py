import subprocess
import sys

from assistant_bench.paths import REPO_ROOT


def test_dependency_stubs_match_the_installed_packages() -> None:
    result = subprocess.run(
        [
            *(sys.executable, '-m', 'mypy.stubtest'),
            *('--mypy-config-file', str(REPO_ROOT / 'pyproject.toml')),
            '--ignore-missing-stub',
            'harbor.llms.chat',
            'harbor.llms.lite_llm',
            'statsmodels.discrete.discrete_model',
            'threadpoolctl',
            'zhon.cedict.simplified',
            'zhon.cedict.traditional',
            'zhon.hanzi',
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
