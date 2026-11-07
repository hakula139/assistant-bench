import asyncio
import hashlib
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from importlib.resources import files
from pathlib import Path

import pytest
from harbor.job import Job
from harbor.models.job.config import JobConfig
from harbor.models.trial.config import UserAgentConfig
from pydantic import ValidationError

from assistant_bench import canaries, jobs
from assistant_bench.catalog import Api, Price, System, SystemIdentity
from assistant_bench.families import load_cases, load_family
from assistant_bench.harnesses import HOST_ENVIRONMENT, agent_kwargs, fingerprint
from assistant_bench.harnesses.usage import ATTEMPTS_FILE, Attempt, MeasuredUsage
from assistant_bench.messages import InputError
from assistant_bench.paths import DATA_DIR_VAR
from assistant_bench.spend import CanaryAccounting, Entry, write_canary_accounting
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
    response,
    voted_tasks,
    write_trial,
)


pytestmark = pytest.mark.usefixtures('export')


def benchmark(system: System) -> JobConfig:
    family = load_family('smoke-text')
    return jobs.job(family, [system], render_tasks(family, load_cases(family)[:1]))


def prepare_probe(system: System) -> canaries.Probe:
    (probe,) = canaries.prepare([benchmark(system)], {system.name: system})
    return probe


@pytest.fixture
def host_jobs(
    endpoint: StubEndpoint, monkeypatch: pytest.MonkeyPatch
) -> list[JobConfig]:
    for name, value in endpoint.env.items():
        monkeypatch.setenv(name, value)
    configs: list[JobConfig] = []

    async def execute(config: JobConfig) -> None:
        await (await Job.create(config)).run()

    def run_job(config: JobConfig, *, log: Path | None = None) -> Path:
        assert log == config.jobs_dir / f'{config.job_name}.log'
        configs.append(config)
        asyncio.run(execute(config))
        return jobs.job_path(config)

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
            self, price: Price | None, expected: float | None
        ) -> None:
            system = make_system(
                price=price, context_window=100_000, max_output_tokens=10_000
            )
            probe = prepare_probe(system)

            ceiling = probe.ceiling_usd

            if expected is None:
                assert ceiling is None
            else:
                assert ceiling == pytest.approx(expected)


class TestPrepare:
    def test_probes_pending_systems_once_across_families(self) -> None:
        family = load_family('smoke-text')
        other = family.model_copy(update={'name': 'other'})
        cases = load_cases(family)[:1]
        large = make_system()
        small = make_system(name='acme-small', route='acme/small')
        configs = [
            jobs.job(family, [large], render_tasks(family, cases)),
            jobs.job(other, [large, small], render_tasks(other, cases)),
        ]
        frozen = [config.model_dump_json() for config in configs]

        probes = canaries.prepare(iter(configs), {s.name: s for s in (large, small)})

        assert [probe.original.name for probe in probes] == ['acme-large', 'acme-small']
        assert all(probe.config.n_attempts == 1 for probe in probes)
        assert all(probe.config.retry.max_retries == 0 for probe in probes)
        assert all(probe.config.n_concurrent_trials == 1 for probe in probes)
        assert all(probe.config.agents[0].n_concurrent == 1 for probe in probes)
        assert [config.model_dump_json() for config in configs] == frozen

    def test_sends_no_probe_for_reused_trials(self, data_dir: Path) -> None:
        system = make_system()
        config = benchmark(system)
        finish_job(config)
        current = benchmark(system)

        probes = canaries.prepare([current], {system.name: system})

        assert probes == []
        assert not (data_dir / 'canaries').exists()

    def test_sends_no_probe_for_trials_that_only_need_regrading(
        self, families_dir: Path, data_dir: Path
    ) -> None:
        family = voted_tasks(families_dir, 'echo')
        system = make_system()
        selected = jobs.family_systems(family, [system])
        task = families_dir / 'f' / 'tasks' / 'echo'
        finish_job(jobs.job(family, selected, [task]))
        break_setting(task, ('task', 'version'), '1.1.0')
        current = jobs.job(family, selected, [task])

        probes = canaries.prepare([current], {system.name: system})

        assert probes == []
        assert not (data_dir / 'canaries').exists()

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
        self, harness: str, api: Api, version: str | None, allowance: int
    ) -> None:
        original = make_system(
            harness=harness,
            api=api,
            version=version,
            max_output_tokens=allowance,
            context_window=100_000,
            prompt='Use a concise answer.',
            thinking_mode='anthropic-adaptive' if api == 'anthropic-messages' else None,
        )
        frozen = original.model_dump()

        probe = prepare_probe(original)

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
        first = prepare_probe(system)
        monkeypatch.setenv(DATA_DIR_VAR, str(tmp_path / 'elsewhere'))

        second = prepare_probe(system)

        assert jobs.job_path(first.config) != jobs.job_path(second.config)
        assert first.config.tasks[0].path != second.config.tasks[0].path
        assert first.protocol_digest == second.protocol_digest

    @pytest.mark.parametrize('field', ['job_name', 'jobs_dir', 'tasks'])
    def test_protocol_ignores_each_relocated_job_or_task_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
    ) -> None:
        system = make_system()
        first = prepare_probe(system)
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

        second = prepare_probe(system)

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
        self, monkeypatch: pytest.MonkeyPatch, field: str, value: object
    ) -> None:
        system = make_system()
        first = prepare_probe(system)
        changed = first.config.model_copy(update={field: value})

        def probe_job(system: System, task: Path, root: Path) -> JobConfig:
            return changed

        monkeypatch.setattr(jobs, 'probe_job', probe_job)

        second = prepare_probe(system)

        assert first.protocol_digest != second.protocol_digest

    def test_protocol_tracks_the_instruction_revision(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        system = make_system()
        first = prepare_probe(system)
        instruction = files('assistant_bench').joinpath('canary.md').read_text()
        (tmp_path / 'canary.md').write_text(instruction + '\nAdditional inert text.\n')

        def resources(package: str) -> Path:
            return tmp_path

        monkeypatch.setattr(canaries, 'files', resources)

        second = prepare_probe(system)

        assert first.source_generate == second.source_generate
        assert first.protocol_digest != second.protocol_digest

    def test_protocol_is_stable_across_independent_process_hash_seeds(self) -> None:
        system = make_system()
        config = benchmark(system)

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
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        system = make_system()
        original = jobs.probe_job
        instructions = ['Use a formal tone.', 'Finish with a question.']

        def probe_job(system: System, task: Path, root: Path) -> JobConfig:
            return original(system, task, root).model_copy(
                update={'extra_instructions': list(instructions)}
            )

        monkeypatch.setattr(jobs, 'probe_job', probe_job)
        first = prepare_probe(system)
        instructions.reverse()

        second = prepare_probe(system)

        assert first.protocol_digest != second.protocol_digest


class TestRun:
    @pytest.mark.parametrize('catalog', ['changed', 'removed'])
    def test_recovers_interrupted_spend_from_frozen_accounting_before_any_new_job(
        self,
        data_dir: Path,
        host_jobs: list[JobConfig],
        monkeypatch: pytest.MonkeyPatch,
        catalog: str,
    ) -> None:
        system = make_system(context_window=100_000, max_output_tokens=10_000)
        interrupted = prepare_probe(system)
        directory = interrupted.config.jobs_dir / 'orphaned-job'
        write_canary_accounting(directory, interrupted.actual, interrupted.ceiling_usd)
        attempt = Attempt(
            trial='interrupted__1',
            system=system.name,
            fingerprint=fingerprint(interrupted.actual),
            started_at=START,
            finished_at=START,
            responses=[MeasuredUsage(100, 0, 0, 40, None), None],
        )
        directory.mkdir()
        (directory / ATTEMPTS_FILE).write_text((attempt.model_dump_json() + '\n') * 2)
        ledger = data_dir / 'spend' / 'canaries.jsonl'
        assert not ledger.exists()
        run_job = jobs.run_job

        def checked_run(config: JobConfig, *, log: Path | None = None) -> Path:
            (recovered,) = [
                Entry.model_validate_json(line)
                for line in ledger.read_text().splitlines()
            ]
            assert recovered.job == 'orphaned-job'
            assert recovered.price == system.price
            assert recovered.ceiling_usd == pytest.approx(0.27048)
            return run_job(config, log=log)

        monkeypatch.setattr(jobs, 'run_job', checked_run)
        changed = system.model_copy(
            update={
                'price': Price(input=10, output=20, cache_read=1, cache_write=12),
                'max_output_tokens': 1024,
            }
        )
        probes = [prepare_probe(changed)] if catalog == 'changed' else []

        canaries.run(probes)
        frozen = ledger.read_bytes()
        assert canaries.run([]) == []

        entries = [
            Entry.model_validate_json(line) for line in ledger.read_text().splitlines()
        ]
        recovered = next(entry for entry in entries if entry.job == 'orphaned-job')
        assert recovered.responses == attempt.responses
        assert recovered.fingerprint == fingerprint(interrupted.actual)
        assert recovered.price == system.price
        assert recovered.ceiling_usd == pytest.approx(0.27048)
        assert ledger.read_bytes() == frozen
        if catalog == 'changed':
            current = next(entry for entry in entries if entry.job != 'orphaned-job')
            assert current.price == changed.price
            assert current.ceiling_usd == pytest.approx(1.22048)
            assert host_jobs == [probes[0].config]
        else:
            assert entries == [recovered]
            assert host_jobs == []

    @pytest.mark.parametrize('failure', ['outcomes', 'append'])
    def test_reconciles_spend_even_when_an_outcome_cannot_be_saved(
        self, data_dir: Path, monkeypatch: pytest.MonkeyPatch, failure: str
    ) -> None:
        system = make_system()
        probe = prepare_probe(system)
        attempt = Attempt(
            trial='canary__1',
            system=system.name,
            fingerprint=fingerprint(probe.actual),
            started_at=START,
            finished_at=START,
            responses=[MeasuredUsage(100, 0, 0, 40, None)],
        )

        def run_job(config: JobConfig, *, log: Path | None = None) -> Path:
            directory = jobs.job_path(config)
            snapshot = Path(f'{directory}.accounting.json')
            accounting = CanaryAccounting.model_validate_json(snapshot.read_text())
            assert accounting.system == system.name
            assert accounting.price == system.price
            assert accounting.ceiling_usd == probe.ceiling_usd
            assert not directory.exists()
            directory.mkdir()
            (directory / ATTEMPTS_FILE).write_text(attempt.model_dump_json() + '\n')
            if failure == 'outcomes':
                trial = directory / 'canary__1'
                trial.mkdir()
                (trial / 'result.json').write_text('{malformed')
            else:
                key = hashlib.sha256(system.name.encode()).hexdigest()
                (data_dir / 'canaries' / f'{key}.jsonl').mkdir()
            return directory

        monkeypatch.setattr(jobs, 'run_job', run_job)

        with pytest.raises(
            ValidationError if failure == 'outcomes' else IsADirectoryError
        ):
            canaries.run([probe])

        (entry,) = [
            Entry.model_validate_json(line)
            for line in (data_dir / 'spend' / 'canaries.jsonl').read_text().splitlines()
        ]
        assert entry.responses == attempt.responses
        assert entry.price == system.price
        assert entry.ceiling_usd == probe.ceiling_usd

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
    ) -> None:
        first, second = [make_system(name=name) for name in names]
        endpoint.response = completion('READY') | {'model': 'first-2026'}
        canaries.run([prepare_probe(first)])
        endpoint.response = completion('READY') | {'model': 'second-2026'}
        canaries.run([prepare_probe(second)])
        endpoint.response = completion('READY') | {'model': 'first-2026'}

        canaries.run([prepare_probe(first)])

        assert [
            record.response_models for record in canaries.read_records(first.name)
        ] == [
            ['first-2026'],
            ['first-2026'],
        ]
        (other,) = canaries.read_records(second.name)
        assert other.system == second.name
        assert other.response_models == ['second-2026']
        assert all(
            record.system == first.name for record in canaries.read_records(first.name)
        )
        entries = [
            Entry.model_validate_json(line)
            for line in (data_dir / 'spend' / 'canaries.jsonl').read_text().splitlines()
        ]
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
            thinking_mode='anthropic-adaptive' if api == 'anthropic-messages' else None,
        )
        probe = prepare_probe(original)

        (record,) = canaries.run([probe])

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
        assert canaries.read_records(original.name) == [record]
        (entry,) = [
            Entry.model_validate_json(line)
            for line in (data_dir / 'spend' / 'canaries.jsonl').read_text().splitlines()
        ]
        assert entry.responses == [record.usage]
        assert entry.ceiling_usd == pytest.approx(0.04096)
        assert not (data_dir / 'spend' / 'smoke-text.jsonl').exists()

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
    ) -> None:
        system = make_system(
            api=api,
            thinking_mode='anthropic-adaptive' if api == 'anthropic-messages' else None,
        )
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
        probe = prepare_probe(system)

        with pytest.raises(InputError, match='canary probe failed') as error:
            canaries.run([probe])

        assert 'differs from its baseline' not in str(error.value)
        assert 'backend-private-47' not in str(error.value)
        (failed,) = canaries.read_records(system.name)
        assert not failed.success
        if failure == 'private-finish':
            assert failed.finish_reason == 'backend-private-47'
        (entry,) = [
            Entry.model_validate_json(line)
            for line in (data_dir / 'spend' / 'canaries.jsonl').read_text().splitlines()
        ]
        assert entry.fingerprint == fingerprint(probe.actual)
        assert len(endpoint.requests) == len(host_jobs) == 1

        endpoint.status = 200
        endpoint.stream = (
            message_stream(message('READY')) if api == 'anthropic-messages' else None
        )
        endpoint.response = (
            response('READY') if api == 'openai-responses' else completion('READY')
        )
        (succeeded,) = canaries.run([prepare_probe(system)])

        assert succeeded.success
        assert [record.success for record in canaries.read_records(system.name)] == [
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
    ) -> None:
        system = make_system()
        baseline = completion('READY')
        baseline['usage']['prompt_tokens'] = baseline_tokens
        baseline['usage']['total_tokens'] = baseline_tokens + 40
        baseline['usage']['prompt_tokens_details'] = {'cached_tokens': 0}
        endpoint.response = baseline
        canaries.run([prepare_probe(system)])
        payload = completion('READY')
        payload['usage']['prompt_tokens'] = tokens
        payload['usage']['total_tokens'] = tokens + 40
        payload['usage']['prompt_tokens_details'] = {'cached_tokens': 0}
        payload['model'] = model
        endpoint.response = payload
        current = prepare_probe(system)

        if refused:
            with pytest.raises(InputError, match='differs from its baseline') as error:
                canaries.run([current])
            assert model not in str(error.value)
            assert 'large-2026' not in str(error.value)
        else:
            canaries.run([current])

        assert len(endpoint.requests) == len(host_jobs) == 2
        assert [
            r.usage.input_tokens for r in canaries.read_records(system.name) if r.usage
        ] == [baseline_tokens, tokens]
        assert canaries.read_records(system.name)[-1].response_models == [model]

    @pytest.mark.parametrize('changed', ['source', 'protocol'])
    def test_starts_a_new_baseline_when_source_or_protocol_changes(
        self, endpoint: StubEndpoint, host_jobs: list[JobConfig], changed: str
    ) -> None:
        system = make_system()
        (first,) = canaries.run([prepare_probe(system)])
        endpoint.response = completion('READY') | {'model': 'other-2026'}
        if changed == 'source':
            system = system.model_copy(update={'prompt': 'Answer briefly.'})
            probe = prepare_probe(system)
        else:
            probe = replace(prepare_probe(system), protocol_digest='different-protocol')

        (current,) = canaries.run([probe])

        assert current.success
        assert len(endpoint.requests) == len(host_jobs) == 2
        assert (
            current.source_generate_fingerprint != first.source_generate_fingerprint
            or current.probe_protocol_digest != first.probe_protocol_digest
        )

    def test_keeps_the_first_baseline_after_an_accepted_small_change(
        self, endpoint: StubEndpoint, host_jobs: list[JobConfig]
    ) -> None:
        system = make_system()
        for tokens in (50, 52):
            payload = completion('READY')
            payload['usage']['prompt_tokens'] = tokens
            payload['usage']['total_tokens'] = tokens + 40
            payload['usage']['prompt_tokens_details'] = {'cached_tokens': 0}
            endpoint.response = payload
            canaries.run([prepare_probe(system)])
        payload['usage']['prompt_tokens'] = 54
        payload['usage']['total_tokens'] = 94
        endpoint.response = payload

        with pytest.raises(InputError, match='Input tokens: `50` → `54`'):
            canaries.run([prepare_probe(system)])

        assert len(endpoint.requests) == len(host_jobs) == 3
        assert all(record.success for record in canaries.read_records(system.name))

    def test_records_every_selected_probe_before_refusing_the_run(
        self,
        endpoint: StubEndpoint,
        host_jobs: list[JobConfig],
        data_dir: Path,
    ) -> None:
        systems = [make_system(name=name) for name in ('large', 'small')]
        probes = [prepare_probe(system) for system in systems]
        endpoint.statuses = [503]

        with pytest.raises(InputError, match='canary probe failed'):
            canaries.run(probes)

        assert [canaries.read_records(s.name)[0].success for s in systems] == [
            False,
            True,
        ]
        assert len(endpoint.requests) == len(host_jobs) == 2
        entries = [
            Entry.model_validate_json(line)
            for line in (data_dir / 'spend' / 'canaries.jsonl').read_text().splitlines()
        ]
        assert {entry.system for entry in entries} == {'large', 'small'}

    @pytest.mark.parametrize('failure', ['interrupt', 'harbor-error', 'trial-error'])
    def test_keeps_an_unfinished_attempt_and_its_spend_before_refusing(
        self, data_dir: Path, monkeypatch: pytest.MonkeyPatch, failure: str
    ) -> None:
        system = make_system()
        probe = prepare_probe(system)
        attempt = Attempt(
            trial='canary__1',
            system=system.name,
            fingerprint=fingerprint(probe.actual),
            started_at=START,
            finished_at=START,
            responses=[None],
        )

        def fail(config: JobConfig, *, log: Path | None = None) -> Path:
            directory = jobs.job_path(config)
            directory.mkdir(parents=True)
            (directory / ATTEMPTS_FILE).write_text(attempt.model_dump_json() + '\n')
            if failure == 'interrupt':
                raise KeyboardInterrupt
            if failure == 'trial-error':
                write_trial(
                    directory,
                    'canary__1',
                    config.agents[0],
                    task='canaries/fixed',
                    instruction=None,
                    version='1.0.0',
                    error='backend-private-47',
                )
                return directory
            raise subprocess.CalledProcessError(3, 'backend-private-47')

        monkeypatch.setattr(jobs, 'run_job', fail)

        if failure == 'interrupt':
            with pytest.raises(KeyboardInterrupt):
                canaries.run([probe])
        else:
            with pytest.raises(InputError, match='canary probe failed') as error:
                canaries.run([probe])
            assert 'backend-private-47' not in str(error.value)

        (record,) = canaries.read_records(system.name)
        assert (
            record.error
            == {
                'interrupt': 'InterruptedProbe',
                'harbor-error': 'CalledProcessError',
                'trial-error': 'backend-private-47',
            }[failure]
        )
        assert not record.success
        (entry,) = [
            Entry.model_validate_json(line)
            for line in (data_dir / 'spend' / 'canaries.jsonl').read_text().splitlines()
        ]
        assert entry.responses == [None]
        assert entry.fingerprint == fingerprint(probe.actual)
