"""Harbor trials under Docker, which the Harbor Smoke job runs."""

from collections import Counter
from pathlib import Path

import pytest

from assistant_bench import jobs
from assistant_bench.catalog import NixosConfigExport
from assistant_bench.families import case_task, load_cases, load_family
from assistant_bench.paths import FAMILIES_DIR
from support import StubEndpoint, make_system, terminus_completion


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
    assert Counter((o.system, o.error, o.reward) for o in outputs) == Counter(
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
        jobs.run_job(jobs.case_job(family, [system], tasks))

    outputs = jobs.ingest(family, {system.name: system})
    assert [(o.case, o.error, o.text, o.output_tokens) for o in outputs] == [
        ('en-greeting', None, 'Hello there.', 40),
        ('zh-greeting', None, 'Hello there.', 40),
    ]
    prompts = [request.body['messages'][0]['content'] for request in endpoint.requests]
    assert sorted(prompts) == sorted(case.instruction for case in cases)


def test_terminus_completes_a_smoke_task_through_the_endpoint(
    endpoint: StubEndpoint,
    export: NixosConfigExport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in endpoint.env.items():
        monkeypatch.setenv(name, value)
    task = FAMILIES_DIR / 'smoke' / 'tasks' / 'echo'
    endpoint.response = terminus_completion(solution_commands(task))
    family = load_family('smoke')
    system = make_system(harness='terminus-2')

    job_dir = jobs.run_job(jobs.case_job(family, [system], [task]))

    (output,) = jobs.read_trials(family, job_dir, {})
    assert (output.case, output.system, output.error, output.reward) == (
        'echo',
        'acme-large',
        None,
        1.0,
    )
    assert output.nixos_config_rev == export.rev
    assert endpoint.requests
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
