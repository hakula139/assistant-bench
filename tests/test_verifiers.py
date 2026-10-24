import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from assistant_bench.paths import REPO_ROOT


CODING = REPO_ROOT / 'families' / 'coding-repo'
PROBE = REPO_ROOT / 'tests' / 'fixtures' / 'verifiers' / 'probe.py'

MALFORMED_RESULTS: list[object] = [
    [],
    {'parse_user': {}},
    {'parse_user': [None]},
    {'parse_user': [{'ok': 1}]},
    {'parse_user': [{'ok': 'value', 'extra': True}]},
    {'parse_user': [{'error': 'ValueError', 'message': 'bad', 'attrs': []}]},
    {
        'parse_user': [
            {
                'error': 'ValueError',
                'message': 'bad',
                'attrs': [],
                'is_value_error': True,
            }
        ]
    },
    {
        'parse_user': [
            {
                'error': 'ValueError',
                'message': 'bad',
                'attrs': [None, None, None],
                'is_value_error': 1,
            }
        ]
    },
]


def probe(tmp_path: Path, *args: str) -> object:
    result = subprocess.run(
        [sys.executable, str(PROBE), *args],
        capture_output=True,
        check=True,
        env={
            'PATH': os.environ['PATH'],
            'PYTHONPATH': str(CODING / 'shared' / 'tests'),
            'PYTHONDONTWRITEBYTECODE': '1',
            'TMPDIR': str(tmp_path),
        },
        text=True,
        timeout=15,
    )
    decoded: object = json.loads(result.stdout)
    return decoded


@pytest.fixture
def probe_contract() -> types.ModuleType:
    path = CODING / 'tasks' / 'refactor-parsers' / 'tests' / 'probe_contract.py'
    spec = importlib.util.spec_from_file_location('probe_contract', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestReadResults:
    def test_preserves_every_expected_probe_outcome(
        self, probe_contract: types.ModuleType
    ) -> None:
        path = CODING / 'tasks' / 'refactor-parsers' / 'tests' / 'expected.json'
        expected: object = json.loads(path.read_text())

        actual: object = probe_contract.read_results(path.read_text())

        assert actual == expected

    @pytest.mark.parametrize(
        'payload',
        MALFORMED_RESULTS,
        ids=[
            'root',
            'entries',
            'outcome',
            'success-value',
            'extra-field',
            'missing-field',
            'attributes',
            'boolean',
        ],
    )
    def test_rejects_malformed_candidate_outcomes(
        self, probe_contract: types.ModuleType, payload: object
    ) -> None:
        with pytest.raises(ValueError):
            probe_contract.read_results(json.dumps(payload))


class TestIgnoreOption:
    @pytest.mark.parametrize('target', ['--top', '--stopwords'])
    @pytest.mark.parametrize(
        'scope',
        ['parser', 'group', 'exclusive', 'group-exclusive', 'child', 'child-group'],
    )
    def test_ignores_only_the_target_across_native_containers(
        self, tmp_path: Path, target: str, scope: str
    ) -> None:
        top = target == '--top'
        stopwords = target == '--stopwords'
        cases: list[tuple[int, str, int | None, list[str]]] = [
            (9, 'keep.txt', None, []),
            (9 if top else 4, 'keep.txt', None, []),
            (9 if top else 5, 'keep.txt', None, []),
            (9 if top else 7, 'keep.txt', None, []),
            (9, 'keep.txt' if stopwords else 'skip.txt', None, []),
            (9, 'keep.txt' if stopwords else 'skip.txt', None, []),
            (9, 'keep.txt' if stopwords else 'last.txt', None, []),
            (9, 'keep.txt', 6, []),
        ]
        if not scope.startswith('child'):
            cases.append((9, 'keep.txt', None, ['--top=4']))
        expected: list[dict[str, object]] = []
        for limit, stop, other, tail in cases:
            values: dict[str, object] = {
                'top': limit,
                'stopwords': stop,
                'other': other,
                'tail': tail,
            }
            if scope.startswith('child'):
                values['command'] = 'run'
            expected.append(values)

        actual = probe(tmp_path, 'option', target, scope)

        assert actual == expected

    @pytest.mark.parametrize(
        ('case', 'expected'),
        [
            ('default', {'target': 'default'}),
            ('suppressed', {}),
            ('initial', {'target': 'initial'}),
            ('typed-unused', {'target': 7}),
            ('flag-unused', {'target': False}),
            ('flag-used', {'target': False}),
        ],
    )
    def test_preserves_native_defaults(
        self, tmp_path: Path, case: str, expected: dict[str, object]
    ) -> None:
        actual = probe(tmp_path, 'default', case)

        assert actual == expected

    def test_preserves_custom_constructors_and_untouched_sibling_actions(
        self, tmp_path: Path
    ) -> None:
        actual = probe(tmp_path, 'shared-action')

        assert actual == {
            'first': {'target': 11, 'other': 6},
            'second': {'target': 13, 'other': 8},
            'constructors': 4,
        }


class TestAfterImport:
    @pytest.mark.parametrize('name', ['resource_fixture', 'resource_fixture.core'])
    def test_preserves_native_resources_and_existing_function_aliases(
        self, tmp_path: Path, name: str
    ) -> None:
        actual = probe(tmp_path, 'loader', name)

        assert actual == {
            'value': 'changed',
            'alias': 'changed',
            'callbacks': 1,
            'executions': 1,
            'during': 'native resource payload\n',
            'after': 'native resource payload\n',
            'modern': 'native resource payload\n',
            'native_loader': True,
        }


class TestTopWordsVerifier:
    @pytest.mark.parametrize(
        ('omitted', 'detected'),
        [
            (set[str](), (True, True)),
            ({'test_top_with_stopwords'}, (True, False)),
            ({'test_top', 'test_top_with_stopwords'}, (False, False)),
        ],
        ids=['complete', 'no-stopwords', 'no-cli-features'],
    )
    def test_requires_tests_that_exercise_each_cli_feature(
        self, tmp_path: Path, omitted: set[str], detected: tuple[bool, bool]
    ) -> None:
        task = CODING / 'tasks' / 'add-top-words'
        workspace = tmp_path / 'app'
        shutil.copytree(task / 'environment' / 'app', workspace)
        env = {
            'PATH': os.environ['PATH'],
            'BENCH_APP_DIR': str(workspace),
            'TMPDIR': str(tmp_path),
            'PYTHONDONTWRITEBYTECODE': '1',
        }
        solution = (
            (task / 'solution' / 'solve.sh')
            .read_text()
            .replace('cd /app', 'cd "$BENCH_APP_DIR"')
        )
        subprocess.run(['bash', '-s'], input=solution, env=env, text=True, check=True)
        tests = workspace / 'tests' / 'test_cli.py'
        tree = ast.parse(tests.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                node.body = [
                    item
                    for item in node.body
                    if not isinstance(item, ast.FunctionDef) or item.name not in omitted
                ]
        tests.write_text(ast.unparse(tree))

        result = subprocess.run(
            [sys.executable, str(task / 'tests' / 'verify.py')],
            capture_output=True,
            check=False,
            env=env,
            text=True,
            timeout=60,
        )

        assert (result.returncode == 0) is all(detected), result.stdout + result.stderr
        assert "PASS the agent's test suite passes" in result.stdout
        assert 'PASS the hidden tests pass' in result.stdout
        assert (
            'PASS the test suite fails when top_words finds no words' in result.stdout
        )
        for covered, option in zip(detected, ('--top', '--stopwords'), strict=True):
            verdict = 'PASS' if covered else 'FAIL'
            assert (
                f'{verdict} the test suite fails when the CLI ignores {option}'
                in result.stdout
            )
        assert 'AttributeError' not in result.stdout + result.stderr
