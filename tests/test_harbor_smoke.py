"""Harbor trials under Docker, which the Harbor Smoke job runs."""

import json
import threading
from collections import Counter
from collections.abc import Callable
from importlib import metadata
from pathlib import Path
from typing import Any

import pytest
from harbor.models.trial.paths import TrialPaths
from harbor.models.trial.result import TrialResult

from assistant_bench import collection, execution, jobs
from assistant_bench.catalog import NixosConfigExport
from assistant_bench.families import case_task, load_cases, load_family
from assistant_bench.harnesses.endpoint import HEADERS_VAR
from assistant_bench.messages import InputError
from assistant_bench.runtime import RunContext
from assistant_bench.spend import Accounting, Entry
from support import (
    StubEndpoint,
    make_system,
    message,
    message_stream,
    reasoned_response,
    response,
    run_host_job,
    terminus_completion,
    write_task_family,
)


pytestmark = [pytest.mark.docker, pytest.mark.usefixtures('export')]


@pytest.fixture
def installed_downloads(loopback_requests_only: set[tuple[str, str]]) -> None:
    downloads = loopback_requests_only
    for platform in ('linux-arm64', 'linux-x64'):
        downloads.add(
            (
                'downloads.claude.ai',
                f'/claude-code-releases/2.1.319/{platform}/claude',
            )
        )
    downloads.add(
        ('downloads.claude.ai', '/claude-code-releases/2.1.319/manifest.json')
    )
    downloads.add(('api.github.com', '/repos/openai/codex/releases/tags/rust-v0.167.0'))
    for architecture in ('aarch64', 'x86_64'):
        downloads.add(
            (
                'github.com',
                '/openai/codex/releases/download/rust-v0.167.0/'
                f'codex-{architecture}-unknown-linux-musl.tar.gz',
            )
        )
    for asset in (
        '7b6f4118-1fd3-4666-a2a7-651f4fa02f3d',
        '8d61505c-e73b-41b2-b8d8-28d9ef5843c5',
    ):
        downloads.add(
            (
                'release-assets.githubusercontent.com',
                f'/github-production-release-asset/965415649/{asset}',
            )
        )


def solution_commands(task: Path) -> str:
    """Return the task's solution as newline-terminated terminal input."""
    lines = (task / 'solution' / 'solve.sh').read_text().splitlines()
    return (
        '\n'.join(
            line
            for line in lines
            if line.strip() and not line.startswith(('#!', 'set -'))
        )
        + '\n'
    )


def reply_text(completion: dict[str, Any]) -> str:
    content: str = completion['choices'][0]['message']['content']
    return content


def serve_chat_turns(endpoint: StubEndpoint, commands: str) -> dict[str, Any]:
    """Answer every request with the terminal commands, returning that reply."""
    endpoint.response = terminus_completion(commands)
    return endpoint.response


def serve_responses_turns(endpoint: StubEndpoint, commands: str) -> dict[str, Any]:
    """Queue the commands, then a final answer, returning the first reply."""
    endpoint.responses = [
        reasoned_response(reply_text(terminus_completion(commands)), 'resp-tool'),
        reasoned_response(
            reply_text(terminus_completion()), 'resp-final', 'final_answer'
        ),
    ]
    return endpoint.responses[0]


def serve_messages_turns(endpoint: StubEndpoint, commands: str) -> dict[str, Any]:
    """Stream the commands, then a final answer, returning the first message."""
    first = message(reply_text(terminus_completion(commands)), message_id='tool')
    final = message(reply_text(terminus_completion()), message_id='final')
    endpoint.streams = [message_stream(first), message_stream(final)]
    return first


def test_check_passes_the_oracle_and_fails_the_nop_on_every_smoke_task(
    tmp_path: Path,
) -> None:
    family = load_family('smoke')
    tasks = [case_task(family, case.id) for case in load_cases(family)]

    job_dir = jobs.run_job(jobs.check_job(family, tasks, root=tmp_path))

    outputs = collection.read_trials(family, job_dir)
    assert Counter(
        (output.system, output.error, output.reward) for output in outputs
    ) == Counter(
        {
            ('oracle', None, 1.0): jobs.CHECK_ATTEMPTS,
            ('nop', None, 0.0): jobs.CHECK_ATTEMPTS,
        }
    )


@pytest.mark.parametrize(
    ('api', 'effort', 'thinking_mode', 'serve_turns'),
    [
        pytest.param(
            'openai-chat-completions',
            'medium',
            None,
            serve_chat_turns,
            id='openai-chat-completions',
        ),
        pytest.param(
            'openai-responses',
            'max',
            None,
            serve_responses_turns,
            id='openai-responses',
        ),
        pytest.param(
            'anthropic-messages',
            'high',
            'anthropic-adaptive',
            serve_messages_turns,
            id='anthropic-messages',
        ),
    ],
)
def test_terminus_runs_a_voted_task_family_once_and_regrades_a_tests_edit(
    endpoint: StubEndpoint,
    export: NixosConfigExport,
    families_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_context: RunContext,
    api: str,
    effort: str,
    thinking_mode: str | None,
    serve_turns: Callable[[StubEndpoint, str], dict[str, Any]],
) -> None:
    for name, value in endpoint.env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(HEADERS_VAR, '{"x-trace":"synthetic-docker-trace"}')
    monkeypatch.setattr(jobs, 'run_job', run_host_job)
    write_task_family(families_dir, 'echo', systems=('acme-large',))
    family = load_family('f')
    task = case_task(family, 'echo')
    first = serve_turns(endpoint, solution_commands(task))
    system = make_system(
        api=api,
        effort=effort,
        thinking_mode=thinking_mode,
        max_output_tokens=32768,
        context_window=131072,
    )
    systems = jobs.family_systems(family, [system])

    config = jobs.job(
        family,
        systems,
        [task],
        root=run_context.root,
        sources=run_context.sources(family),
    )
    execution.execute(
        run_context,
        config,
        {system.name: Accounting.of(system)},
        scope=family.name,
        run_id='first-run',
        expected_source_revision=run_context.source_revision(family),
    )
    job_dir = jobs.job_path(config)
    run_context.refresh_sources(family)
    requests = len(endpoint.requests)
    execution.execute(
        run_context,
        jobs.job(
            family,
            systems,
            [task],
            root=run_context.root,
            sources=run_context.sources(family),
        ),
        {system.name: Accounting.of(system)},
        scope=family.name,
        run_id='second-run',
        expected_source_revision=run_context.source_revision(family),
    )

    assert len(endpoint.requests) == requests > 0
    (output,) = collection.collect(family, {system.name: system}, run_context).outputs
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
        assert request.headers['x-trace'] == 'synthetic-docker-trace'
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
        assert endpoint.requests[1].body['input'][1:3] == first['output']
    elif api == 'anthropic-messages':
        assert len(endpoint.requests) == 2
        assert endpoint.requests[1].body['messages'][1] == {
            'role': 'assistant',
            'content': first['content'],
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
    manifest = task / 'task.toml'
    manifest.write_text(
        manifest.read_text().replace('version = "1.0.0"', 'version = "1.1.0"')
    )
    execution.execute(
        run_context,
        jobs.job(
            family,
            systems,
            [task],
            root=run_context.root,
            sources=run_context.sources(family),
        ),
        {system.name: Accounting.of(system)},
        scope=family.name,
        run_id='third-run',
        expected_source_revision=run_context.source_revision(family),
    )
    run_context.refresh_sources(family)

    assert len(endpoint.requests) == requests
    (regraded,) = collection.collect(family, {system.name: system}, run_context).outputs
    assert regraded == output


@pytest.mark.parametrize(
    ('harness', 'version', 'api'),
    [
        ('claude-code', '2.1.319', 'anthropic-messages'),
        ('codex', '0.167.0', 'openai-responses'),
    ],
)
@pytest.mark.usefixtures('installed_downloads')
@pytest.mark.parametrize('cancelled', [False, True], ids=['finished', 'cancelled'])
def test_installed_cli_preserves_native_trace_usage_and_task_diff(
    endpoint: StubEndpoint,
    families_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_context: RunContext,
    harness: str,
    version: str,
    api: str,
    cancelled: bool,
) -> None:
    for name, value in endpoint.env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(HEADERS_VAR, '{"x-trace":"synthetic-docker-trace"}')
    monkeypatch.setattr(jobs, 'run_job', run_host_job)
    write_task_family(families_dir, 'echo', systems=('acme-large',))
    family = load_family('f')
    task = case_task(family, 'echo')
    command = solution_commands(task).strip()
    if cancelled:
        task_config = task / 'task.toml'
        task_config.write_text(
            task_config.read_text().replace('timeout_sec = 120.0', 'timeout_sec = 15.0')
        )
    if harness == 'claude-code':
        first = message(stop_reason='tool_use', message_id='tool')
        first['content'] = [
            {
                'type': 'tool_use',
                'id': 'tool-1',
                'name': 'Bash',
                'input': {'command': command},
            }
        ]
        final = message('Done.', message_id='final')
        final['content'] = [{'type': 'text', 'text': 'Done.'}]
        endpoint.streams = [message_stream(first), message_stream(final)]
    else:
        first = response(None, response_id='resp-tool')
        first['output'] = [
            {
                'type': 'function_call',
                'id': 'fc-1',
                'call_id': 'call-1',
                'name': 'exec_command',
                'arguments': json.dumps({'cmd': command}),
                'status': 'completed',
            }
        ]
        final = response('Done.', response_id='resp-final')
        endpoint.streams = []
        for payload in (first, final):
            events: list[dict[str, Any]] = [
                {'type': 'response.created', 'response': {**payload, 'output': []}},
                *[
                    {
                        'type': 'response.output_item.done',
                        'output_index': index,
                        'item': item,
                    }
                    for index, item in enumerate(payload['output'])
                ],
                {'type': 'response.completed', 'response': payload},
            ]
            endpoint.streams.append(
                [f'data: {json.dumps(event)}\n\n'.encode() for event in events]
            )
    endpoint.stream = endpoint.streams[-1]
    system = make_system(
        api=api,
        harness=harness,
        version=version,
        thinking_mode='anthropic-adaptive' if harness == 'claude-code' else None,
        max_output_tokens=32768,
        context_window=131072,
    )
    selected = jobs.family_systems(family, [system])
    config = jobs.job(
        family,
        selected,
        [task],
        root=run_context.root,
        sources=run_context.sources(family),
    )
    if cancelled:
        endpoint.response_gate = threading.Event()
        endpoint.response_gate_after = 1

    try:
        if cancelled:
            with pytest.raises(ExceptionGroup) as failed:
                execution.execute(
                    run_context,
                    config,
                    {system.name: Accounting.of(system)},
                    scope=family.name,
                    run_id='installed-run',
                    expected_source_revision=run_context.source_revision(family),
                )
            assert (
                failed.value.subgroup(
                    lambda error: (
                        isinstance(error, InputError)
                        and str(error)
                        == 'A trial has uncertain accounting and cannot settle.'
                    )
                )
                is not None
            )
        else:
            execution.execute(
                run_context,
                config,
                {system.name: Accounting.of(system)},
                scope=family.name,
                run_id='installed-run',
                expected_source_revision=run_context.source_revision(family),
            )
    finally:
        if endpoint.response_gate is not None:
            endpoint.response_gate.set()

    job_dir = jobs.job_path(config)
    run_context.refresh_sources(family)
    (output,) = collection.collect(family, {system.name: system}, run_context).outputs
    if cancelled:
        assert output.error == 'AgentTimeoutError'
        assert endpoint.response_waiting.is_set()
        assert run_context.state.run_status('installed-run') == 'uncertain'
    else:
        assert output.error is None
        assert output.reward == 1.0
        assert output.text is not None and '+hello' in output.text
    turns = 1 if cancelled else 2
    assert output.input_tokens == (
        None if cancelled else turns * (130 if harness == 'claude-code' else 120)
    )
    assert output.output_tokens == (None if cancelled else turns * 40)
    assert list(job_dir.rglob('trajectory.json'))
    (attempt,) = [
        Entry.model_validate_json(value)
        for value in run_context.state.entries(family.name)
    ]
    measured = [usage for usage in attempt.responses if usage is not None]
    assert all(usage.output_tokens == 40 for usage in measured)
    assert len(measured) == turns
    assert attempt.responses.count(None) == int(cancelled)
    key = endpoint.key_file.read_text().strip().encode()
    assert not [
        path
        for path in job_dir.rglob('*')
        if path.is_file() and key in path.read_bytes()
    ]
