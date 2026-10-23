"""Harbor trials under Docker, which the Harbor Smoke job runs."""

from collections import Counter

import pytest

from assistant_bench import jobs
from assistant_bench.families import case_task, load_cases, load_family
from support import StubEndpoint, make_system


pytestmark = [pytest.mark.docker, pytest.mark.usefixtures('export')]


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
