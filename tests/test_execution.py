import asyncio
import json
import shutil
from collections.abc import Coroutine
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from harbor.models.job.config import JobConfig
from harbor.models.trial.config import TrialConfig
from harbor.models.trial.result import TrialResult
from harbor.trial.queue import TrialQueue

from assistant_bench import collection, execution, jobs
from assistant_bench.artifacts import ArtifactRef
from assistant_bench.catalog import System
from assistant_bench.database import D1Error, SQLiteDatabase
from assistant_bench.families import Family, load_cases, load_family
from assistant_bench.harnesses.endpoint import HEADERS_VAR
from assistant_bench.messages import InputError
from assistant_bench.runtime import RunContext
from assistant_bench.spend import Accounting, Entry, read_ledger
from assistant_bench.state import AttemptStart, Claim, State
from assistant_bench.tasks import render_tasks
from support import (
    START,
    StubEndpoint,
    break_setting,
    executed_trial,
    make_system,
    run_host_job,
    write_votes_family,
)


@pytest.fixture
def family(families_dir: Path) -> Family:
    write_votes_family(families_dir, ['acme-large'], {'greeting': 'message'})
    return load_family('f')


@pytest.fixture
def system(endpoint: StubEndpoint, monkeypatch: pytest.MonkeyPatch) -> System:
    for name, value in endpoint.env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(
        HEADERS_VAR, json.dumps({'x-trace': 'synthetic-execution-trace-value'})
    )
    return make_system()


def planned(context: RunContext, family: Family, system: System) -> JobConfig:
    sources = context.sources(family)
    tasks = render_tasks(
        family,
        load_cases(family, private_root=context.private_root),
        root=context.root,
    )
    return jobs.job(family, [system], tasks, root=context.root, sources=sources)


class TestExecute:
    @pytest.mark.usefixtures('export')
    def test_a_fresh_client_recovers_outputs_and_reuses_without_another_request(
        self,
        run_context: RunContext,
        private_store: SQLiteDatabase,
        family: Family,
        system: System,
        endpoint: StubEndpoint,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(jobs, 'run_job', run_host_job)
        config = planned(run_context, family, system)

        execution.execute(
            run_context,
            config,
            {system.name: Accounting.of(system)},
            scope=family.name,
            run_id='first-run',
            expected_source_revision=run_context.source_revision(family),
        )

        assert run_context.sources(family) == []
        run_context.refresh_sources(family)
        first = collection.collect(family, {system.name: system}, run_context)
        ledger = read_ledger(family.name, run_context.state)
        assert first.outputs[0].text == 'Hello there.'
        assert isinstance(ledger[0], Entry)
        assert run_context.state.run_status('first-run') == 'complete'
        shutil.rmtree(run_context.root)

        root = tmp_path / 'fresh-client'
        root.mkdir()
        private_root = run_context.artifacts.source(
            run_context.case_revision, tmp_path / 'fresh-private'
        )
        fresh = RunContext(
            root=root,
            private_root=private_root,
            case_revision=run_context.case_revision,
            code_revision=run_context.code_revision,
            state=State(private_store),
            artifacts=run_context.artifacts,
        )
        recovered = collection.collect(family, {system.name: system}, fresh)
        replay = planned(fresh, family, system).model_copy(
            update={'job_name': 'replay'}
        )
        execution.execute(
            fresh,
            replay,
            {system.name: Accounting.of(system)},
            scope=family.name,
            run_id='second-run',
            expected_source_revision=fresh.source_revision(family),
        )

        assert recovered.outputs == first.outputs
        assert read_ledger(family.name, fresh.state) == ledger
        assert len(endpoint.requests) == 1
        assert fresh.state.run_status('second-run') == 'complete'

        expanded = family.model_copy(
            update={'settings': family.settings.model_copy(update={'samples': 2})}
        )
        config = planned(fresh, expanded, system).model_copy(
            update={'job_name': 'expanded'}
        )
        execution.execute(
            fresh,
            config,
            {system.name: Accounting.of(system)},
            scope=family.name,
            run_id='expanded-run',
            expected_source_revision=fresh.source_revision(expanded),
        )

        assert len(endpoint.requests) == 2
        assert private_store.execute(
            'SELECT slot_key FROM attempt_claims WHERE run_id = ?', ('expanded-run',)
        ).rows == [{'slot_key': '["f/greeting","acme-large",1]'}]

    def test_private_git_authentication_failure_prevents_model_io(
        self,
        run_context: RunContext,
        private_store: SQLiteDatabase,
        family: Family,
        system: System,
        endpoint: StubEndpoint,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = planned(run_context, family, system)
        monkeypatch.setenv('TEST_GH_STATUS', '1')
        monkeypatch.setattr(jobs, 'run_job', run_host_job)

        with pytest.raises(InputError):
            execution.execute(
                run_context,
                config,
                {system.name: Accounting.of(system)},
                scope=family.name,
                run_id='unauthenticated-run',
                expected_source_revision=run_context.source_revision(family),
            )

        assert endpoint.requests == []
        assert private_store.execute('SELECT * FROM attempt_claims').rows == []

    @pytest.mark.usefixtures('export')
    @pytest.mark.parametrize(
        'method', ['record_entry', 'record_trial_artifact', 'settle_claim']
    )
    def test_terminal_persistence_failure_keeps_the_run_uncertain(
        self,
        run_context: RunContext,
        family: Family,
        system: System,
        endpoint: StubEndpoint,
        monkeypatch: pytest.MonkeyPatch,
        method: str,
    ) -> None:
        config = planned(run_context, family, system)

        def fail(*args: object, **kwargs: object) -> None:
            raise D1Error('Synthetic terminal persistence failure.')

        monkeypatch.setattr(State, method, fail)
        monkeypatch.setattr(jobs, 'run_job', run_host_job)

        with pytest.raises(ExceptionGroup) as failure:
            execution.execute(
                run_context,
                config,
                {system.name: Accounting.of(system)},
                scope=family.name,
                run_id='uncertain-run',
                expected_source_revision=run_context.source_revision(family),
            )

        assert failure.value.subgroup(D1Error) is not None
        assert len(endpoint.requests) == 1
        (request,) = run_context.state.requests(family.name)
        assert request['receipt_json'] is not None
        assert run_context.state.claim_state(str(request['claim_id'])) == 'uncertain'
        assert not run_context.state.complete_run('uncertain-run')
        assert run_context.state.run_status('uncertain-run') == 'uncertain'


class TestExecutionPlugin:
    @pytest.mark.usefixtures('export')
    def test_a_mixed_regrade_and_rerun_only_admits_the_new_solution(
        self,
        run_context: RunContext,
        private_store: SQLiteDatabase,
        families_dir: Path,
        system: System,
        endpoint: StubEndpoint,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        write_votes_family(
            families_dir,
            [system.name],
            {'again': 'message', 'fresh': 'message'},
            name='mixed',
        )
        family = load_family('mixed')
        monkeypatch.setattr(jobs, 'run_job', run_host_job)
        first = planned(run_context, family, system).model_copy(
            update={'job_name': 'initial'}
        )
        for task in first.tasks:
            assert task.path is not None
            break_setting(task.path, ('verifier', 'environment_mode'), 'separate')
        execution.execute(
            run_context,
            first,
            {system.name: Accounting.of(system)},
            scope=family.name,
            run_id='original-run',
            expected_source_revision=run_context.source_revision(family),
        )
        run_context.refresh_sources(family)
        tasks = [
            run_context.root / 'tasks' / family.name / name
            for name in ('again', 'fresh')
        ]
        for task in tasks:
            break_setting(task, ('verifier', 'environment_mode'), 'separate')
        break_setting(tasks[0], ('task', 'version'), '1.1.0')
        break_setting(tasks[1], ('task', 'version'), '2.0.0')
        mixed = jobs.job(
            family,
            [system],
            tasks,
            root=run_context.root,
            sources=run_context.sources(family),
        ).model_copy(update={'job_name': 'mixed'})

        execution.execute(
            run_context,
            mixed,
            {system.name: Accounting.of(system)},
            scope=family.name,
            run_id='mixed-run',
            expected_source_revision=run_context.source_revision(family),
        )

        assert len(endpoint.requests) == 3
        assert private_store.execute(
            'SELECT slot_key, state FROM attempt_claims WHERE run_id = ?',
            ('mixed-run',),
        ).rows == [{'slot_key': '["mixed/fresh","acme-large",0]', 'state': 'settled'}]
        assert run_context.state.run_status('mixed-run') == 'complete'
        assert run_context.state.artifact_generation(family.name).revision == 4

    @pytest.mark.usefixtures('export')
    def test_preclaims_all_slots_before_trials_start_in_reverse_order(
        self,
        run_context: RunContext,
        private_store: SQLiteDatabase,
        family: Family,
        system: System,
        endpoint: StubEndpoint,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        family = family.model_copy(
            update={'settings': family.settings.model_copy(update={'samples': 2})}
        )
        config = planned(run_context, family, system).model_copy(
            update={'n_concurrent_trials': 1}
        )
        order: list[str] = []
        begin_attempt = State.begin_attempt
        submit_batch = TrialQueue.submit_batch

        def begin(state: State, attempt: AttemptStart) -> bool:
            if not order:
                assert private_store.execute(
                    'SELECT slot_key, state FROM attempt_claims '
                    'WHERE run_id = ? ORDER BY slot_key',
                    ('reverse-run',),
                ).rows == [
                    {'slot_key': '["f/greeting","acme-large",0]', 'state': 'claimed'},
                    {'slot_key': '["f/greeting","acme-large",1]', 'state': 'claimed'},
                ]
                assert endpoint.requests == []
            order.append(state.get_claim(attempt.claim_id).slot_key)
            return begin_attempt(state, attempt)

        def reverse(
            queue: TrialQueue, configs: list[TrialConfig]
        ) -> list[Coroutine[Any, Any, TrialResult]]:
            return submit_batch(queue, list(reversed(configs)))

        monkeypatch.setattr(State, 'begin_attempt', begin)
        monkeypatch.setattr(TrialQueue, 'submit_batch', reverse)
        monkeypatch.setattr(jobs, 'run_job', run_host_job)

        execution.execute(
            run_context,
            config,
            {system.name: Accounting.of(system)},
            scope=family.name,
            run_id='reverse-run',
            expected_source_revision=run_context.source_revision(family),
        )

        assert order == [
            '["f/greeting","acme-large",1]',
            '["f/greeting","acme-large",0]',
        ]
        assert len(endpoint.requests) == 2
        assert run_context.state.run_status('reverse-run') == 'complete'

    @pytest.mark.usefixtures('export')
    def test_a_pinned_plan_cannot_pay_after_another_trial_is_registered(
        self,
        run_context: RunContext,
        family: Family,
        system: System,
        endpoint: StubEndpoint,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        stale = planned(run_context, family, system).model_copy(
            update={'job_name': 'stale-plan'}
        )
        admitted = stale.model_copy(update={'job_name': 'registered-first'})

        async def register() -> None:
            async with executed_trial(
                run_context, admitted, system, scope=family.name
            ) as trial:
                (trial.paths.agent_dir / 'diagnostic.txt').write_text(
                    'Synthetic setup diagnostic.'
                )

        asyncio.run(register())
        monkeypatch.setattr(jobs, 'run_job', run_host_job)
        assert run_context.source_revision(family) == 0
        assert run_context.state.artifact_generation(family.name).revision == 1

        with pytest.raises(InputError, match='already owns a planned paid slot'):
            execution.execute(
                run_context,
                stale,
                {system.name: Accounting.of(system)},
                scope=family.name,
                run_id='stale-run',
                expected_source_revision=run_context.source_revision(family),
            )

        assert endpoint.requests == []
        assert read_ledger(family.name, run_context.state) == []

    def test_an_ambiguous_second_preclaim_cancels_both_before_model_io(
        self,
        run_context: RunContext,
        family: Family,
        system: System,
        endpoint: StubEndpoint,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        family = family.model_copy(
            update={'settings': family.settings.model_copy(update={'samples': 2})}
        )
        config = planned(run_context, family, system)
        granted: list[str] = []
        claim_attempt = State.claim_attempt

        def ambiguous(state: State, claim: Claim) -> bool:
            result = claim_attempt(state, claim)
            assert result
            granted.append(claim.id)
            if len(granted) == 2:
                raise D1Error('Synthetic admission response lost.')
            return result

        monkeypatch.setattr(State, 'claim_attempt', ambiguous)
        monkeypatch.setattr(jobs, 'run_job', run_host_job)

        with pytest.raises(D1Error, match='Synthetic admission response lost'):
            execution.execute(
                run_context,
                config,
                {system.name: Accounting.of(system)},
                scope=family.name,
                run_id='failed-admission',
                expected_source_revision=run_context.source_revision(family),
            )

        assert endpoint.requests == []
        assert [run_context.state.claim_state(claim) for claim in granted] == [
            'cancelled',
            'cancelled',
        ]
        assert read_ledger(family.name, run_context.state) == []

    @pytest.mark.parametrize('phase', ['setup', 'pending-request'])
    def test_archives_failure_diagnostics_without_claiming_paid_settlement(
        self,
        run_context: RunContext,
        family: Family,
        system: System,
        endpoint: StubEndpoint,
        tmp_path: Path,
        phase: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = planned(run_context, family, system)
        claim_id: str | None = None
        diagnostic = b'Synthetic diagnostic bytes.\r\n'

        async def fail() -> None:
            nonlocal claim_id
            async with executed_trial(
                run_context, config, system, scope=family.name, run_id='failed-run'
            ) as trial:
                claim_id = trial.recorder.claim_id
                (trial.paths.agent_dir / 'diagnostic.txt').write_bytes(diagnostic)
                if phase == 'setup':
                    raise RuntimeError('Synthetic setup failure.')
                trial.recorder.begin(START)
                await trial.recorder.admit('openai-responses')
                trial.recorder.finish(START + timedelta(seconds=1))
                raise RuntimeError('Synthetic interruption.')

        expected = RuntimeError if phase == 'setup' else InputError
        with pytest.raises(expected):
            asyncio.run(fail())

        assert claim_id is not None
        assert run_context.state.claim_state(claim_id) == (
            'cancelled' if phase == 'setup' else 'uncertain'
        )
        assert bool(read_ledger(family.name, run_context.state)) == (phase != 'setup')
        assert run_context.state.complete_run('failed-run') == (phase == 'setup')
        (artifact,) = run_context.state.artifact_generation(family.name).artifacts
        archive = run_context.artifacts.materialize(
            ArtifactRef(
                commit_sha=artifact.commit_sha,
                path=artifact.path,
                manifest_digest=artifact.manifest_digest,
            ),
            tmp_path / 'recovered-diagnostics',
        )
        restored = (
            archive.job / archive.manifest.trial_name / 'agent' / 'diagnostic.txt'
        )
        assert restored.read_bytes() == diagnostic
        if phase == 'pending-request':
            run_context.refresh_sources(family)
            retry = planned(run_context, family, system).model_copy(
                update={'job_name': 'interrupted-retry'}
            )
            monkeypatch.setattr(jobs, 'run_job', run_host_job)

            with pytest.raises(InputError, match='already owns a planned paid slot'):
                execution.execute(
                    run_context,
                    retry,
                    {system.name: Accounting.of(system)},
                    scope=family.name,
                    run_id='interrupted-retry',
                    expected_source_revision=run_context.source_revision(family),
                )

            assert endpoint.requests == []
