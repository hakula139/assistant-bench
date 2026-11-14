import json
import os
import shutil
import subprocess
import sys
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import replace
from importlib.resources import files
from pathlib import Path
from unittest.mock import Mock

import pytest
from harbor.models.job.config import JobConfig, SourceJobConfig
from harbor.models.trial.config import UserAgentConfig

from assistant_bench import canaries, execution, jobs
from assistant_bench.catalog import Api, Price, System, SystemIdentity
from assistant_bench.database import SQLiteDatabase
from assistant_bench.families import load_cases, load_family
from assistant_bench.harnesses import HOST_ENVIRONMENT, agent_kwargs, fingerprint
from assistant_bench.harnesses.endpoint import SECRET_HEADERS_FILE_VAR
from assistant_bench.harnesses.generate import OUTPUT_FILE
from assistant_bench.messages import InputError
from assistant_bench.paths import REPO_ROOT
from assistant_bench.runtime import RunContext
from assistant_bench.spend import Entry, read_ledger
from assistant_bench.state import ArtifactGeneration, Run, State
from assistant_bench.tasks import render_tasks
from support import (
    FIXTURES,
    START,
    StubEndpoint,
    break_setting,
    completion,
    finish_job,
    make_system,
    message,
    message_stream,
    private_database,
    response,
    run_host_job,
    voted_tasks,
)


pytestmark = pytest.mark.usefixtures('export')


def benchmark(
    system: System, root: Path, *, previous: JobConfig | None = None
) -> JobConfig:
    family = load_family('smoke-text')
    sources: list[tuple[SourceJobConfig, Path]] = []
    if previous is not None:
        path = jobs.job_path(previous)
        sources.append((SourceJobConfig(action='diff', type='local', path=path), path))
    return jobs.job(
        family,
        [system],
        render_tasks(family, load_cases(family)[:1], root=root),
        root=root,
        sources=sources,
    )


def prepare_probe(
    system: System, root: Path, *, source_root: Path = REPO_ROOT
) -> canaries.Probe:
    (probe,) = canaries.prepare(
        [benchmark(system, root)],
        {system.name: system},
        root=root,
        source_root=source_root,
    )
    return probe


@pytest.fixture
def host_jobs(
    endpoint: StubEndpoint,
    run_context: RunContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> list[JobConfig]:
    for name, value in endpoint.env.items():
        monkeypatch.setenv(name, value)
    secret_file = tmp_path / 'secret-headers.json'
    secret_file.write_text('{"x-session-token":"synthetic-canary-trace-unique-value"}')
    monkeypatch.setenv(SECRET_HEADERS_FILE_VAR, str(secret_file))
    configs: list[JobConfig] = []

    def run_job(
        config: JobConfig,
        *,
        log: Path | None = None,
        plugin_context: Path | None = None,
        source_root: Path | None = None,
    ) -> Path:
        assert log == config.jobs_dir / f'{config.job_name}.log'
        assert plugin_context is not None
        configs.append(config)
        return run_host_job(
            config,
            log=log,
            plugin_context=plugin_context,
            source_root=source_root,
        )

    monkeypatch.setattr(jobs, 'run_job', run_job)
    return configs


class TestProbe:
    class TestCeilingUsd:
        @pytest.mark.parametrize(
            ('price', 'expected'),
            [
                (Price(input=2, output=10, cache_read=0.2, cache_write=2.5), 0.27048),
                (Price(input=3, output=10, cache_read=0.2, cache_write=2.5), 0.32048),
                (Price(input=2, output=10, cache_read=0.2), None),
                (None, None),
            ],
            ids=['write-rate', 'input-rate', 'unknown-write-rate', 'unpriced'],
        )
        def test_uses_the_actual_probe_allowance_and_dearest_input_rate(
            self, price: Price | None, expected: float | None, tmp_path: Path
        ) -> None:
            system = make_system(
                price=price, context_window=100_000, max_output_tokens=10_000
            )
            probe = prepare_probe(system, tmp_path)

            ceiling = probe.ceiling_usd

            if expected is None:
                assert ceiling is None
            else:
                assert ceiling == pytest.approx(expected)


class TestPrepare:
    def test_probes_pending_systems_once_across_families(self, tmp_path: Path) -> None:
        family = load_family('smoke-text')
        other = family.model_copy(update={'name': 'other'})
        cases = load_cases(family)[:1]
        large = make_system()
        small = make_system(name='acme-small', route='acme/small')
        configs = [
            jobs.job(
                family,
                [large],
                render_tasks(family, cases, root=tmp_path),
                root=tmp_path,
                sources=[],
            ),
            jobs.job(
                other,
                [large, small],
                render_tasks(other, cases, root=tmp_path),
                root=tmp_path,
                sources=[],
            ),
        ]
        frozen = [config.model_dump_json() for config in configs]

        probes = canaries.prepare(
            iter(configs), {s.name: s for s in (large, small)}, root=tmp_path
        )

        assert [probe.original.name for probe in probes] == ['acme-large', 'acme-small']
        assert all(probe.config.n_attempts == 1 for probe in probes)
        assert all(probe.config.retry.max_retries == 0 for probe in probes)
        assert all(probe.config.n_concurrent_trials == 1 for probe in probes)
        assert all(probe.config.agents[0].n_concurrent == 1 for probe in probes)
        assert [config.model_dump_json() for config in configs] == frozen

    def test_sends_no_probe_for_reused_trials(self, tmp_path: Path) -> None:
        system = make_system()
        config = benchmark(system, tmp_path)
        finish_job(config)
        current = benchmark(system, tmp_path, previous=config)

        probes = canaries.prepare([current], {system.name: system}, root=tmp_path)

        assert probes == []
        assert not (tmp_path / 'canaries').exists()

    def test_sends_no_probe_for_trials_that_only_need_regrading(
        self, families_dir: Path, tmp_path: Path
    ) -> None:
        family = voted_tasks(families_dir, 'echo')
        system = make_system()
        selected = jobs.family_systems(family, [system])
        task = families_dir / 'f' / 'tasks' / 'echo'
        initial = jobs.job(family, selected, [task], root=tmp_path, sources=[])
        finish_job(initial)
        break_setting(task, ('task', 'version'), '1.1.0')
        path = jobs.job_path(initial)
        current = jobs.job(
            family,
            selected,
            [task],
            root=tmp_path,
            sources=[(SourceJobConfig(action='diff', type='local', path=path), path)],
        )

        probes = canaries.prepare([current], {system.name: system}, root=tmp_path)

        assert probes == []
        assert not (tmp_path / 'canaries').exists()

    @pytest.mark.parametrize(
        ('harness', 'api', 'version', 'allowance'),
        [
            ('generate', 'openai-chat-completions', None, 64),
            ('terminus-2', 'openai-chat-completions', None, 10_000),
            ('codex', 'openai-responses', '1.2.3', 10_000),
            ('claude-code', 'anthropic-messages', '1.2.3', 10_000),
        ],
        ids=['generate', 'terminal', 'responses-cli', 'messages-cli'],
    )
    def test_keeps_original_and_generate_identities_separate(
        self,
        harness: str,
        api: Api,
        version: str | None,
        allowance: int,
        tmp_path: Path,
    ) -> None:
        original = make_system(
            harness=harness,
            api=api,
            version=version,
            max_output_tokens=allowance,
            context_window=100_000,
            prompt='Use a concise answer.',
        )
        frozen = original.model_dump()

        probe = prepare_probe(original, tmp_path)

        source = original.model_copy(update={'harness': 'generate', 'version': None})
        actual = source.model_copy(update={'max_output_tokens': min(2048, allowance)})
        assert probe.original == original
        assert original.model_dump() == frozen
        assert probe.source_generate == source
        assert probe.actual == actual
        assert probe.config.agents[0].kwargs == agent_kwargs(actual)
        assert probe.config.agents[0].model_name == original.route
        assert probe.config.environment.import_path == HOST_ENVIRONMENT
        assert probe.config.verifier.disable
        assert probe.config.source_jobs == []

    def test_protocol_ignores_ephemeral_job_and_data_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        system = make_system(max_output_tokens=4096)
        first = prepare_probe(system, tmp_path / 'first')

        second = prepare_probe(system, tmp_path / 'elsewhere')

        assert jobs.job_path(first.config) != jobs.job_path(second.config)
        assert first.config.tasks[0].path != second.config.tasks[0].path
        assert first.protocol_digest == second.protocol_digest

    @pytest.mark.parametrize('field', ['job_name', 'jobs_dir', 'tasks'])
    def test_protocol_ignores_each_relocated_job_or_task_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
    ) -> None:
        system = make_system()
        first = prepare_probe(system, tmp_path)
        if field == 'job_name':
            relocated = first.config.model_copy(update={'job_name': 'relocated-job'})
        elif field == 'jobs_dir':
            relocated = first.config.model_copy(update={'jobs_dir': tmp_path / 'jobs'})
        else:
            task = first.config.tasks[0]
            assert task.path is not None
            destination = tmp_path / 'relocated-task'
            shutil.copytree(task.path, destination)
            relocated = first.config.model_copy(
                update={'tasks': [task.model_copy(update={'path': destination})]}
            )

        def probe_job(system: System, task: Path, root: Path) -> JobConfig:
            return relocated

        monkeypatch.setattr(jobs, 'probe_job', probe_job)

        second = prepare_probe(system, tmp_path)

        assert first.protocol_digest == second.protocol_digest

    @pytest.mark.parametrize(
        ('field', 'value'),
        [
            ('timeout_multiplier', 2.0),
            ('agent_timeout_multiplier', 2.0),
            ('verifier_timeout_multiplier', 2.0),
            ('agent_setup_timeout_multiplier', 2.0),
            ('environment_build_timeout_multiplier', 2.0),
            ('extra_instructions', ['Answer in one sentence.']),
            (
                'user_agent',
                UserAgentConfig.model_validate(
                    {'name': 'nop', 'bridge': {'kind': 'acp'}}
                ),
            ),
        ],
        ids=[
            'timeout',
            'agent-timeout',
            'verifier-timeout',
            'agent-setup-timeout',
            'environment-build-timeout',
            'extra-instructions',
            'simulated-user',
        ],
    )
    def test_protocol_tracks_the_resolved_stable_job_configuration(
        self, monkeypatch: pytest.MonkeyPatch, field: str, value: object, tmp_path: Path
    ) -> None:
        system = make_system()
        first = prepare_probe(system, tmp_path)
        changed = first.config.model_copy(update={field: value})

        def probe_job(system: System, task: Path, root: Path) -> JobConfig:
            return changed

        monkeypatch.setattr(jobs, 'probe_job', probe_job)

        second = prepare_probe(system, tmp_path)

        assert first.protocol_digest != second.protocol_digest

    def test_protocol_tracks_the_instruction_revision(self, tmp_path: Path) -> None:
        system = make_system()
        first = prepare_probe(system, tmp_path)
        instruction = files('assistant_bench').joinpath('canary.md').read_text()
        source = tmp_path / 'src' / 'assistant_bench'
        source.mkdir(parents=True)
        (source / 'canary.md').write_text(instruction + '\nAdditional inert text.\n')

        second = prepare_probe(system, tmp_path, source_root=tmp_path)

        assert first.source_generate == second.source_generate
        assert first.protocol_digest != second.protocol_digest

    def test_protocol_is_stable_across_independent_process_hash_seeds(
        self, tmp_path: Path
    ) -> None:
        system = make_system()
        config = benchmark(system, tmp_path)

        digests: list[str] = []
        for seed in ('1', '2', '3'):
            result = subprocess.run(
                [
                    sys.executable,
                    str(FIXTURES / 'canary-protocol.py'),
                    system.model_dump_json(),
                    config.model_dump_json(),
                ],
                env=os.environ | {'PYTHONHASHSEED': seed},
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            digests.append(result.stdout.strip())

        assert digests[0] == digests[1] == digests[2]

    def test_protocol_preserves_the_order_of_extra_instructions(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        system = make_system()
        original = jobs.probe_job
        instructions = ['Use a formal tone.', 'Finish with a question.']

        def probe_job(system: System, task: Path, root: Path) -> JobConfig:
            return original(system, task, root).model_copy(
                update={'extra_instructions': list(instructions)}
            )

        monkeypatch.setattr(jobs, 'probe_job', probe_job)
        first = prepare_probe(system, tmp_path)
        instructions.reverse()

        second = prepare_probe(system, tmp_path)

        assert first.protocol_digest != second.protocol_digest


class TestRun:
    def test_reads_the_audited_archive_after_live_trial_files_change(
        self,
        endpoint: StubEndpoint,
        host_jobs: list[JobConfig],
        run_context: RunContext,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        endpoint.response = completion('READY')
        secret = endpoint.key_file.read_text().strip()
        probe = prepare_probe(make_system(), tmp_path)
        submitted = jobs.run_job
        changed: list[Path] = []

        def run_job(
            config: JobConfig,
            *,
            log: Path | None = None,
            plugin_context: Path | None = None,
            source_root: Path | None = None,
        ) -> Path:
            path = submitted(
                config,
                log=log,
                plugin_context=plugin_context,
                source_root=source_root,
            )
            (result,) = path.glob('*/result.json')
            payload = json.loads(result.read_text())
            payload['agent_result']['metadata'].update(
                response_model=secret, finish_reason=secret, request_id=secret
            )
            result.write_text(json.dumps(payload))
            (result.parent / 'agent' / OUTPUT_FILE).write_text(secret)
            changed.append(result)
            return path

        monkeypatch.setattr(jobs, 'run_job', run_job)

        (record,) = canaries.run([probe], run_context)

        assert record.success
        assert record.output == 'READY'
        assert record.finish_reason == 'stop'
        assert record.response_models == ['large-2026']
        assert secret not in record.model_dump_json()
        assert (
            run_context.state.baseline(probe.original.name, canaries.context_key(probe))
            == record.model_dump_json()
        )
        assert canaries.read_records(probe.original.name, run_context.state) == [record]
        assert secret in changed[0].read_text()
        assert len(endpoint.requests) == len(host_jobs) == 1

    @pytest.mark.parametrize('echo', ['output', 'model', 'finish-reason', 'request-id'])
    def test_an_unaudited_probe_retains_only_sanitized_durable_accounting(
        self,
        endpoint: StubEndpoint,
        host_jobs: list[JobConfig],
        run_context: RunContext,
        tmp_path: Path,
        echo: str,
    ) -> None:
        secret = endpoint.key_file.read_text().strip()
        payload = completion('READY')
        if echo == 'output':
            payload['choices'][0]['message']['content'] = f'Provider echoed {secret}.'
        elif echo == 'model':
            payload['model'] = f'provider-{secret}'
        elif echo == 'finish-reason':
            payload['choices'][0]['finish_reason'] = f'provider-{secret}'
        else:
            endpoint.response_headers['x-request-id'] = secret
        endpoint.response = payload
        probe = prepare_probe(make_system(), tmp_path)

        with pytest.raises((ExceptionGroup, InputError)):
            canaries.run([probe], run_context)

        (record,) = canaries.read_records(probe.original.name, run_context.state)
        (entry,) = read_ledger('canaries', run_context.state)
        assert isinstance(entry, Entry)
        assert record.usage == entry.responses[0]
        assert record.usage is not None
        assert (record.usage.input_tokens, record.usage.output_tokens) == (120, 40)
        assert record.response_models == ([] if echo == 'model' else ['large-2026'])
        assert record.output == ''
        assert record.finish_reason is None
        assert record.latency_sec is None
        assert record.error == 'UnauditedTrial'
        assert not record.success
        assert secret not in record.model_dump_json()
        assert secret not in entry.model_dump_json()
        assert (
            run_context.state.baseline(probe.original.name, canaries.context_key(probe))
            is None
        )
        assert run_context.state.artifact_generation('canaries').artifacts == ()
        assert len(endpoint.requests) == len(host_jobs) == 1

    def test_refuses_before_paid_execution_when_the_run_cannot_be_recorded(
        self, tmp_path: Path, run_context: RunContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        probe = prepare_probe(make_system(), tmp_path)
        launch = Mock(
            side_effect=AssertionError('An unrecorded run started paid work.')
        )
        monkeypatch.setattr(jobs, 'run_job', launch)
        monkeypatch.setattr(
            run_context.state,
            'create_run',
            Mock(side_effect=InputError('The run could not be recorded.')),
        )

        with pytest.raises(InputError):
            canaries.run([probe], run_context)

        launch.assert_not_called()
        assert canaries.read_records(probe.original.name, run_context.state) == []
        assert read_ledger('canaries', run_context.state) == []

    @pytest.mark.parametrize('failure', ['interrupt', 'harbor-error'])
    def test_keeps_a_failed_outcome_on_its_run_when_another_run_arrives(
        self,
        tmp_path: Path,
        run_context: RunContext,
        monkeypatch: pytest.MonkeyPatch,
        failure: str,
        private_store: SQLiteDatabase,
    ) -> None:
        probe = prepare_probe(make_system(), tmp_path)
        recorded: list[str] = []

        def fail(
            context: RunContext,
            config: JobConfig,
            accounting: dict[str, object],
            *,
            scope: str,
            run_id: str,
            expected_source_revision: int,
            log: Path | None = None,
        ) -> Run:
            parent = Run(
                run_id,
                scope,
                config.job_name,
                context.code_revision,
                context.case_revision,
                START.isoformat(),
            )
            context.state.create_run(parent)
            recorded.append(run_id)
            context.state.create_run(
                replace(parent, id='unrelated', job='concurrent-job')
            )
            if failure == 'interrupt':
                raise KeyboardInterrupt
            raise subprocess.CalledProcessError(3, 'example-command')

        monkeypatch.setattr(execution, 'execute', fail)

        with pytest.raises(KeyboardInterrupt if failure == 'interrupt' else InputError):
            canaries.run([probe], run_context)

        (outcome,) = canaries.read_records(probe.original.name, run_context.state)
        assert outcome.job == probe.config.job_name
        assert outcome.error == 'UnauditedTrial'
        assert not outcome.success
        assert run_context.state.run(recorded[0]).job == outcome.job
        assert private_store.execute('SELECT run_id FROM canary_records').rows == [
            {'run_id': recorded[0]}
        ]
        assert outcome.job != 'concurrent-job'
        assert read_ledger('canaries', run_context.state) == []

    @pytest.mark.parametrize(
        'names',
        [('../spend/canaries', 'vendor/model'), ('Model', 'model')],
        ids=['path-like', 'case-distinct'],
    )
    def test_keeps_independent_baselines_without_overwriting_the_spend_ledger(
        self,
        endpoint: StubEndpoint,
        host_jobs: list[JobConfig],
        data_dir: Path,
        names: tuple[str, str],
        tmp_path: Path,
        run_context: RunContext,
    ) -> None:
        first, second = [make_system(name=name) for name in names]
        endpoint.response = completion('READY') | {'model': 'first-2026'}
        canaries.run([prepare_probe(first, tmp_path)], run_context)
        endpoint.response = completion('READY') | {'model': 'second-2026'}
        canaries.run([prepare_probe(second, tmp_path)], run_context)
        endpoint.response = completion('READY') | {'model': 'first-2026'}

        canaries.run([prepare_probe(first, tmp_path)], run_context)

        assert [
            record.response_models
            for record in canaries.read_records(first.name, run_context.state)
        ] == [
            ['first-2026'],
            ['first-2026'],
        ]
        (other,) = canaries.read_records(second.name, run_context.state)
        assert other.system == second.name
        assert other.response_models == ['second-2026']
        assert all(
            record.system == first.name
            for record in canaries.read_records(first.name, run_context.state)
        )
        entries = read_ledger('canaries', run_context.state)
        assert [entry.system for entry in entries].count(first.name) == 2
        assert [entry.system for entry in entries].count(second.name) == 1
        assert all(entry.responses[0] is not None for entry in entries)
        assert len(endpoint.requests) == len(host_jobs) == 3

    @pytest.mark.parametrize(
        'api',
        ['openai-chat-completions', 'openai-responses', 'anthropic-messages'],
    )
    def test_records_one_native_request_with_the_actual_probe_identity(
        self,
        endpoint: StubEndpoint,
        host_jobs: list[JobConfig],
        data_dir: Path,
        api: Api,
        tmp_path: Path,
        run_context: RunContext,
    ) -> None:
        if api == 'openai-responses':
            endpoint.response = response('READY')
        elif api == 'anthropic-messages':
            endpoint.stream = message_stream(message('READY'))
        else:
            endpoint.response = completion('READY')
        original = make_system(
            api=api,
            max_output_tokens=4096,
        )
        probe = prepare_probe(original, tmp_path)

        (record,) = canaries.run([probe], run_context)

        (sent,) = endpoint.requests
        limit_key = {
            'openai-chat-completions': 'max_completion_tokens',
            'openai-responses': 'max_output_tokens',
            'anthropic-messages': 'max_tokens',
        }[api]
        assert sent.body['model'] == original.route
        assert sent.body[limit_key] == 2048
        assert host_jobs == [probe.config]
        assert record.success
        assert record.output == 'READY'
        assert record.original_identity == SystemIdentity.model_validate(
            original.model_dump()
        )
        assert record.source_generate_fingerprint == fingerprint(probe.source_generate)
        assert record.actual_probe_fingerprint == fingerprint(probe.actual)
        assert record.actual_probe_identity.max_output_tokens == 2048
        assert record.usage is not None
        assert record.usage.output_tokens == 40
        assert record.usage.reasoning_tokens == (
            None if api == 'anthropic-messages' else 25
        )
        assert canaries.read_records(original.name, run_context.state) == [record]
        (entry,) = read_ledger('canaries', run_context.state)
        assert isinstance(entry, Entry)
        assert entry.responses == [record.usage]
        assert entry.ceiling_usd == pytest.approx(0.04096)
        assert read_ledger('smoke-text', run_context.state) == []

    @pytest.mark.parametrize(
        ('api', 'failure'),
        [
            ('openai-chat-completions', 'length'),
            ('openai-chat-completions', 'refusal'),
            ('openai-chat-completions', 'missing-usage'),
            ('openai-chat-completions', 'http'),
            ('openai-chat-completions', 'private-finish'),
            ('openai-responses', 'incomplete'),
            ('openai-responses', 'refusal'),
            ('anthropic-messages', 'length'),
            ('anthropic-messages', 'refusal'),
        ],
        ids=[
            'chat-length',
            'chat-refusal',
            'missing-usage',
            'http',
            'private-finish',
            'incomplete',
            'responses-refusal',
            'messages-length',
            'messages-refusal',
        ],
    )
    def test_records_failed_attempts_before_refusal_without_establishing_a_baseline(
        self,
        endpoint: StubEndpoint,
        host_jobs: list[JobConfig],
        data_dir: Path,
        api: Api,
        failure: str,
        tmp_path: Path,
        run_context: RunContext,
    ) -> None:
        system = make_system(api=api)
        if api == 'anthropic-messages':
            endpoint.stream = message_stream(
                message(stop_reason='max_tokens' if failure == 'length' else 'refusal')
            )
        elif api == 'openai-responses':
            endpoint.response = response(
                status='incomplete' if failure == 'incomplete' else 'completed',
                incomplete_reason='max_output_tokens'
                if failure == 'incomplete'
                else None,
                refusal='No.' if failure == 'refusal' else None,
            )
        else:
            endpoint.response = completion(
                finish_reason='length' if failure == 'length' else 'stop'
            )
            if failure == 'refusal':
                endpoint.response['choices'][0]['message']['refusal'] = 'No.'
            elif failure == 'missing-usage':
                endpoint.response['usage'] = None
            elif failure == 'http':
                endpoint.status = 503
            elif failure == 'private-finish':
                endpoint.response['choices'][0]['finish_reason'] = 'backend-private-47'
        probe = prepare_probe(system, tmp_path)

        with pytest.raises(InputError, match='canary probe failed') as error:
            canaries.run([probe], run_context)

        assert 'differs from its baseline' not in str(error.value)
        assert 'backend-private-47' not in str(error.value)
        (failed,) = canaries.read_records(system.name, run_context.state)
        assert not failed.success
        if failure == 'private-finish':
            assert failed.finish_reason == 'backend-private-47'
        (entry,) = read_ledger('canaries', run_context.state)
        assert isinstance(entry, Entry)
        assert entry.fingerprint == fingerprint(probe.actual)
        assert len(endpoint.requests) == len(host_jobs) == 1

        endpoint.status = 200
        endpoint.stream = (
            message_stream(message('READY')) if api == 'anthropic-messages' else None
        )
        endpoint.response = (
            response('READY') if api == 'openai-responses' else completion('READY')
        )
        (succeeded,) = canaries.run([prepare_probe(system, tmp_path)], run_context)

        assert succeeded.success
        assert [
            record.success
            for record in canaries.read_records(system.name, run_context.state)
        ] == [
            False,
            True,
        ]
        assert len(endpoint.requests) == len(host_jobs) == 2

    @pytest.mark.parametrize(
        ('baseline_tokens', 'tokens', 'model', 'refused'),
        [
            (50, 52, 'large-2026', False),
            (50, 53, 'large-2026', True),
            (200, 204, 'large-2026', False),
            (200, 205, 'large-2026', True),
            (120, 120, 'backend-private-47', True),
        ],
        ids=[
            'two-token-boundary',
            'over-two-tokens',
            'two-percent-boundary',
            'over-two-percent',
            'different-model',
        ],
    )
    def test_compares_success_to_the_first_successful_matching_baseline(
        self,
        endpoint: StubEndpoint,
        host_jobs: list[JobConfig],
        baseline_tokens: int,
        tokens: int,
        model: str,
        refused: bool,
        tmp_path: Path,
        run_context: RunContext,
    ) -> None:
        system = make_system()
        baseline = completion('READY')
        baseline['usage']['prompt_tokens'] = baseline_tokens
        baseline['usage']['total_tokens'] = baseline_tokens + 40
        baseline['usage']['prompt_tokens_details'] = {'cached_tokens': 0}
        endpoint.response = baseline
        canaries.run([prepare_probe(system, tmp_path)], run_context)
        payload = completion('READY')
        payload['usage']['prompt_tokens'] = tokens
        payload['usage']['total_tokens'] = tokens + 40
        payload['usage']['prompt_tokens_details'] = {'cached_tokens': 0}
        payload['model'] = model
        endpoint.response = payload
        current = prepare_probe(system, tmp_path)

        if refused:
            with pytest.raises(InputError, match='differs from its baseline') as error:
                canaries.run([current], run_context)
            assert model not in str(error.value)
            assert 'large-2026' not in str(error.value)
        else:
            canaries.run([current], run_context)

        assert len(endpoint.requests) == len(host_jobs) == 2
        assert [
            r.usage.input_tokens
            for r in canaries.read_records(system.name, run_context.state)
            if r.usage
        ] == [baseline_tokens, tokens]
        assert canaries.read_records(system.name, run_context.state)[
            -1
        ].response_models == [model]

    @pytest.mark.parametrize('changed', ['source', 'protocol'])
    def test_starts_a_new_baseline_when_source_or_protocol_changes(
        self,
        endpoint: StubEndpoint,
        host_jobs: list[JobConfig],
        changed: str,
        tmp_path: Path,
        run_context: RunContext,
    ) -> None:
        system = make_system()
        original = system
        first_probe = prepare_probe(system, tmp_path)
        (first,) = canaries.run([first_probe], run_context)
        endpoint.response = completion('READY') | {'model': 'other-2026'}
        if changed == 'source':
            system = system.model_copy(update={'prompt': 'Answer briefly.'})
            probe = prepare_probe(system, tmp_path)
        else:
            probe = replace(
                prepare_probe(system, tmp_path), protocol_digest='different-protocol'
            )

        (current,) = canaries.run([probe], run_context)

        assert current.success
        assert (
            current.source_generate_fingerprint != first.source_generate_fingerprint
            or current.probe_protocol_digest != first.probe_protocol_digest
        )
        endpoint.response = completion('READY') | {'model': 'drifted-2026'}
        changed_probe = replace(
            prepare_probe(system, tmp_path), protocol_digest=probe.protocol_digest
        )

        with pytest.raises(InputError, match='differs from its baseline'):
            canaries.run([changed_probe], run_context)
        endpoint.response = completion('READY') | {'model': 'other-2026'}
        with pytest.raises(InputError, match='differs from its baseline'):
            canaries.run([prepare_probe(original, tmp_path)], run_context)

        state = run_context.state
        assert state.baseline(system.name, canaries.context_key(first_probe)) == (
            first.model_dump_json()
        )
        assert state.baseline(system.name, canaries.context_key(probe)) == (
            current.model_dump_json()
        )
        records = canaries.read_records(system.name, state)
        state.reset_baseline(system.name)

        assert state.baseline(system.name, canaries.context_key(first_probe)) is None
        assert state.baseline(system.name, canaries.context_key(probe)) is None
        assert canaries.read_records(system.name, state) == records
        assert len(endpoint.requests) == len(host_jobs) == 4

    def test_keeps_the_first_baseline_after_an_accepted_small_change(
        self,
        endpoint: StubEndpoint,
        host_jobs: list[JobConfig],
        tmp_path: Path,
        run_context: RunContext,
    ) -> None:
        system = make_system()
        for tokens in (50, 52):
            payload = completion('READY')
            payload['usage']['prompt_tokens'] = tokens
            payload['usage']['total_tokens'] = tokens + 40
            payload['usage']['prompt_tokens_details'] = {'cached_tokens': 0}
            endpoint.response = payload
            canaries.run([prepare_probe(system, tmp_path)], run_context)
        payload['usage']['prompt_tokens'] = 54
        payload['usage']['total_tokens'] = 94
        endpoint.response = payload

        with pytest.raises(InputError, match='Input tokens: `50` → `54`'):
            canaries.run([prepare_probe(system, tmp_path)], run_context)

        assert len(endpoint.requests) == len(host_jobs) == 3
        assert all(
            record.success
            for record in canaries.read_records(system.name, run_context.state)
        )

    def test_records_every_selected_probe_before_refusing_the_run(
        self,
        endpoint: StubEndpoint,
        host_jobs: list[JobConfig],
        data_dir: Path,
        tmp_path: Path,
        run_context: RunContext,
    ) -> None:
        systems = [make_system(name=name) for name in ('large', 'small')]
        probes = [prepare_probe(system, tmp_path) for system in systems]
        endpoint.statuses = [503]

        with pytest.raises(InputError, match='canary probe failed'):
            canaries.run(probes, run_context)

        assert [
            canaries.read_records(s.name, run_context.state)[0].success for s in systems
        ] == [
            False,
            True,
        ]
        assert (
            run_context.state.baseline(systems[0].name, canaries.context_key(probes[0]))
            is None
        )
        successful = canaries.read_records(systems[1].name, run_context.state)[0]
        assert (
            run_context.state.baseline(systems[1].name, canaries.context_key(probes[1]))
            == successful.model_dump_json()
        )
        assert len(endpoint.requests) == len(host_jobs) == 2
        entries = read_ledger('canaries', run_context.state)
        assert {entry.system for entry in entries} == {'large', 'small'}

    def test_concurrent_first_probes_compare_with_the_canonical_baseline_winner(
        self,
        endpoint: StubEndpoint,
        host_jobs: list[JobConfig],
        run_context: RunContext,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path = tmp_path / 'shared.sqlite'

        @contextmanager
        def connect(
            target: str, *, credential: str | None = None
        ) -> Generator[SQLiteDatabase]:
            assert target == 'private'
            assert credential is not None
            with private_database(path) as database:
                yield database

        monkeypatch.setattr(execution, 'connect', connect)
        with (
            private_database(path) as first_store,
            private_database(path) as second_store,
        ):
            first = replace(run_context, state=State(first_store))
            second_root = tmp_path / 'second-context'
            second_root.mkdir()
            second = replace(run_context, root=second_root, state=State(second_store))
            system = make_system()
            first_probe = prepare_probe(system, tmp_path / 'first')
            second_probe = prepare_probe(system, tmp_path / 'second')
            key = canaries.context_key(first_probe)
            assert first.state.baseline(system.name, key) is None
            assert second.state.baseline(system.name, key) is None
            generation = State.artifact_generation
            raced = False

            def publish_winner(state: State, scope: str) -> ArtifactGeneration:
                nonlocal raced
                if state is second.state and scope == 'canaries' and not raced:
                    raced = True
                    endpoint.response = completion('READY') | {'model': 'first-2026'}
                    canaries.run([first_probe], first)
                    endpoint.response = completion('READY') | {'model': 'second-2026'}
                return generation(state, scope)

            monkeypatch.setattr(State, 'artifact_generation', publish_winner)

            with pytest.raises(InputError, match='differs from its baseline'):
                canaries.run([second_probe], second)

            winner, contender = canaries.read_records(system.name, second.state)
            assert winner.response_models == ['first-2026']
            assert contender.response_models == ['second-2026']
            assert winner.success and contender.success
            assert second.state.baseline(system.name, key) == winner.model_dump_json()
            assert len(endpoint.requests) == len(host_jobs) == 2
            assert [
                entry.responses for entry in read_ledger('canaries', second.state)
            ] == [[winner.usage], [contender.usage]]
