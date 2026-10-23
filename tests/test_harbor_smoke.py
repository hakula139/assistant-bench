"""Harbor trials under Docker, which the Harbor Smoke job runs."""

from collections import Counter
from importlib import metadata
from pathlib import Path

import pytest

from assistant_bench import jobs
from assistant_bench.catalog import NixosConfigExport
from assistant_bench.families import case_task, load_cases, load_family
from support import (
    StubEndpoint,
    make_system,
    terminus_completion,
    write_task_family,
)


pytestmark = [pytest.mark.docker, pytest.mark.usefixtures('export')]


def solution_commands(task: Path) -> str:
    """Return a task's solution as the keystrokes terminus-2 sends to its terminal.

    Reading the task's own solution makes the reply write wherever the task's
    verifier reads, so the trial follows the task when its output path changes.
    """
    lines = (task / 'solution' / 'solve.sh').read_text().splitlines()
    return (
        '\n'.join(
            line
            for line in lines
            if line.strip() and not line.startswith(('#!', 'set -'))
        )
        + '\n'
    )


def test_check_passes_the_oracle_and_fails_the_nop_on_every_smoke_task() -> None:
    family = load_family('smoke')
    tasks = [case_task(family, case.id) for case in load_cases(family)]

    job_dir = jobs.run_job(jobs.check_job(family, tasks))

    outputs = jobs.read_trials(family, job_dir, {})
    assert Counter(
        (output.system, output.error, output.reward) for output in outputs
    ) == Counter(
        {
            ('oracle', None, 1.0): jobs.CHECK_ATTEMPTS,
            ('nop', None, 0.0): jobs.CHECK_ATTEMPTS,
        }
    )


def test_generate_answers_each_case_once_across_reruns(
    endpoint: StubEndpoint, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in endpoint.env.items():
        monkeypatch.setenv(name, value)
    family = load_family('smoke-text')
    cases = load_cases(family)
    system = make_system()

    for _ in range(2):
        tasks = jobs.render_tasks(family, cases)
        jobs.run_job(jobs.job(family, [system], tasks))

    outputs = jobs.collect(family, {system.name: system})
    assert [
        (output.case, output.error, output.text, output.output_tokens)
        for output in outputs
    ] == [
        ('en-greeting', None, 'Hello there.', 40),
        ('zh-greeting', None, 'Hello there.', 40),
    ]
    prompts = [request.body['messages'][0]['content'] for request in endpoint.requests]
    assert sorted(prompts) == sorted(case.instruction for case in cases)


def test_terminus_runs_a_voted_task_family_once_and_regrades_a_tests_edit(
    endpoint: StubEndpoint,
    export: NixosConfigExport,
    families_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in endpoint.env.items():
        monkeypatch.setenv(name, value)
    write_task_family(families_dir, 'echo', systems=('acme-large',))
    family = load_family('f')
    task = case_task(family, 'echo')
    endpoint.response = terminus_completion(solution_commands(task))
    system = make_system()
    systems = jobs.family_systems(family, [system])

    job_dir = jobs.run_job(jobs.job(family, systems, [task]))
    requests = len(endpoint.requests)
    jobs.run_job(jobs.job(family, systems, [task]))

    assert len(endpoint.requests) == requests > 0
    (output,) = jobs.collect(family, {system.name: system})
    assert output.model_dump(
        include={'case', 'system', 'error', 'reward', 'gate_failures', 'text'}
    ) == {
        'case': 'echo',
        'system': 'acme-large',
        'error': None,
        'reward': 1.0,
        'gate_failures': [],
        'text': '--- /dev/null\n+++ b/greeting.txt\n@@ -0,0 +1 @@\n+hello\n'
        '\\ No newline at end of file\n',
    }
    assert (output.job, output.nixos_config_rev) == (job_dir.name, export.rev)
    assert output.harness_version == metadata.version('harbor')
    for request in endpoint.requests:
        assert request.headers['authorization'] == 'Bearer test-key'
        assert request.headers['x-trace'] == 'bench'
        assert request.body['reasoning_effort'] == 'medium'
        assert request.body['max_completion_tokens'] == 64
    key = endpoint.key_file.read_text().strip().encode()
    assert not [
        path
        for path in job_dir.rglob('*')
        if path.is_file() and key in path.read_bytes()
    ]

    test = task / 'tests' / 'test.sh'
    test.write_text(test.read_text() + '# A tests-only edit.\n')
    config = task / 'task.toml'
    config.write_text(
        config.read_text().replace('version = "1.0.0"', 'version = "1.1.0"')
    )
    jobs.run_job(jobs.job(family, systems, [task]))

    assert len(endpoint.requests) == requests
    (regraded,) = jobs.collect(family, {system.name: system})
    assert regraded == output
