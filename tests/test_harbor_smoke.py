"""Harbor trials under Docker, which the Harbor Smoke job runs."""

from collections import Counter
from importlib import metadata
from pathlib import Path

import pytest
from harbor.models.trial.paths import TrialPaths
from harbor.models.trial.result import TrialResult

from assistant_bench import jobs
from assistant_bench.catalog import NixosConfigExport
from assistant_bench.families import case_task, load_cases, load_family
from support import (
    StubEndpoint,
    make_system,
    message,
    message_stream,
    reasoned_response,
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


@pytest.mark.parametrize(
    'api', ['openai-chat-completions', 'openai-responses', 'anthropic-messages']
)
def test_terminus_runs_a_voted_task_family_once_and_regrades_a_tests_edit(
    endpoint: StubEndpoint,
    export: NixosConfigExport,
    families_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    api: str,
) -> None:
    for name, value in endpoint.env.items():
        monkeypatch.setenv(name, value)
    write_task_family(families_dir, 'echo', systems=('acme-large',))
    family = load_family('f')
    task = case_task(family, 'echo')
    command = terminus_completion(solution_commands(task))
    tool_response = reasoned_response(
        command['choices'][0]['message']['content'], 'resp-tool'
    )
    native_tool = message(
        command['choices'][0]['message']['content'], message_id='tool'
    )
    if api == 'openai-responses':
        final = terminus_completion()
        endpoint.responses = [
            tool_response,
            reasoned_response(
                final['choices'][0]['message']['content'], 'resp-final', 'final_answer'
            ),
        ]
    elif api == 'anthropic-messages':
        final = terminus_completion()
        endpoint.streams = [
            message_stream(native_tool),
            message_stream(
                message(final['choices'][0]['message']['content'], message_id='final')
            ),
        ]
    else:
        endpoint.response = command
    system = make_system(
        api=api,
        effort='max'
        if api == 'openai-responses'
        else 'high'
        if api == 'anthropic-messages'
        else 'medium',
        thinking_mode='anthropic-adaptive' if api == 'anthropic-messages' else None,
        max_output_tokens=32768,
        context_window=131072,
    )
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
        'text': 'diff --git a/greeting.txt b/greeting.txt\nnew file mode 100644\n'
        '--- /dev/null\n+++ b/greeting.txt\n@@ -0,0 +1 @@\n+hello\n'
        '\\ No newline at end of file\n',
    }
    assert (output.job, output.nixos_config_rev) == (job_dir.name, export.rev)
    assert output.harness_version == metadata.version('harbor')
    turns = 2
    assert (output.input_tokens, output.cached_tokens, output.output_tokens) == (
        turns * (130 if api == 'anthropic-messages' else 120),
        turns * 100,
        turns * 40,
    )
    assert output.reasoning_tokens == (
        None if api == 'anthropic-messages' else turns * 25
    )
    timing = TrialResult.model_validate_json(
        TrialPaths(job_dir / output.trial).result_path.read_text()
    ).agent_execution
    assert timing is not None
    assert timing.started_at is not None and timing.finished_at is not None
    assert output.latency_sec == round(
        (timing.finished_at - timing.started_at).total_seconds(), 3
    )
    assert output.cost_usd == pytest.approx(
        turns * (0.000485 if api == 'anthropic-messages' else 0.00046)
    )
    for request in endpoint.requests:
        assert request.headers['authorization'] == 'Bearer test-key'
        assert request.headers['x-trace'] == 'bench'
        if api == 'openai-responses':
            assert request.path == '/v1/responses'
            assert request.body['reasoning'] == {'effort': 'max'}
            assert request.body['max_output_tokens'] == 32768
            assert request.body['store'] is False
            assert 'previous_response_id' not in request.body
            assert 'max_completion_tokens' not in request.body
        elif api == 'anthropic-messages':
            assert request.path == '/anthropic/v1/messages'
            assert request.headers['anthropic-version'] == '2023-06-01'
            assert request.body['output_config'] == {'effort': 'high'}
            assert request.body['thinking'] == {'type': 'adaptive'}
            assert request.body['max_tokens'] == 32768
            assert request.body['stream'] is True
            assert 'tools' not in request.body
        else:
            assert request.body['reasoning_effort'] == 'medium'
            assert request.body['max_completion_tokens'] == 32768
    if api == 'openai-responses':
        assert len(endpoint.requests) == 2
        assert endpoint.requests[1].body['input'][1:3] == tool_response['output']
    elif api == 'anthropic-messages':
        assert len(endpoint.requests) == 2
        assert endpoint.requests[1].body['messages'][1] == {
            'role': 'assistant',
            'content': native_tool['content'],
        }
        assert output.cache_write_tokens == turns * 10
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
