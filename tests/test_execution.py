import asyncio
import json
import shutil
from datetime import timedelta
from pathlib import Path

import pytest
from harbor.models.job.config import JobConfig

from assistant_bench import collection, execution, jobs
from assistant_bench.artifacts import ArtifactRef
from assistant_bench.catalog import System
from assistant_bench.database import SQLiteDatabase
from assistant_bench.families import Family, load_cases, load_family
from assistant_bench.harnesses.endpoint import HEADERS_VAR
from assistant_bench.messages import InputError
from assistant_bench.runtime import RunContext
from assistant_bench.spend import Accounting, Entry, read_ledger
from assistant_bench.state import State
from assistant_bench.tasks import render_tasks
from support import (
    START,
    StubEndpoint,
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
        replay = planned(fresh, family, system)
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


class TestExecutionPlugin:
    @pytest.mark.parametrize('phase', ['setup', 'pending-request'])
    def test_archives_failure_diagnostics_without_claiming_paid_settlement(
        self,
        run_context: RunContext,
        family: Family,
        system: System,
        tmp_path: Path,
        phase: str,
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
