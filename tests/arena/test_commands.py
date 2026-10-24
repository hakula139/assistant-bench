import asyncio
import json
from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from stat import S_IMODE

import pytest
from typer.testing import CliRunner

from arena_support import NativeManagement, native_management, vote_capture
from assistant_bench.arena import commands
from assistant_bench.arena.exports import read_export
from assistant_bench.arena.management import Management
from assistant_bench.arena.publication import load_policy
from assistant_bench.arena.publishing import publish_release
from assistant_bench.arena.runtime import RuntimeTarget, TargetName
from assistant_bench.arena.snapshots import analyze_export
from assistant_bench_arena import management_domain as command
from assistant_bench_arena.ledger_models import Moderation, Receipt, Target
from assistant_bench_arena.runtime_models import Profile
from assistant_bench_arena.serialization import canonical
from ledger_fixtures import make_release
from ledger_support import D1Runtime


runner = CliRunner()


@pytest.fixture(scope='module')
def runtime(tmp_path_factory: pytest.TempPathFactory) -> Iterator[D1Runtime]:
    with D1Runtime(tmp_path_factory.mktemp('native-admin')).owner() as owner:
        yield owner


def use_management(
    monkeypatch: pytest.MonkeyPatch, management: NativeManagement
) -> None:
    @asynccontextmanager
    async def connection(name: str) -> AsyncGenerator[Management]:
        assert name == 'production'
        yield management

    monkeypatch.setattr(commands, '_connection', connection)


class TestConnection:
    def test_refuses_a_valid_different_namespace_before_sending_the_operation(
        self, runtime: D1Runtime, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        async def scenario() -> None:
            async with native_management(runtime) as management:
                target = RuntimeTarget(
                    name='production',
                    manifest_path=tmp_path / 'wrangler.toml',
                    worker_name='synthetic-owner',
                    compatibility_date='2026-10-01',
                    account_id='synthetic-account',
                    database_id='synthetic-database',
                    profile=Profile(
                        namespace='different-valid-namespace',
                        app_origin='https://app.example',
                        render_origin='https://render.example',
                    ),
                )

                def selected(name: TargetName) -> RuntimeTarget:
                    assert name == 'production'
                    return target

                @asynccontextmanager
                async def connection(config: Path) -> AsyncGenerator[Management]:
                    assert config.is_file()
                    yield management

                monkeypatch.setattr(commands, 'load_target', selected)
                monkeypatch.setattr(commands, 'connect', connection)

                result = await asyncio.to_thread(
                    runner.invoke, commands.app, ['status', 'production', 'writing']
                )

                assert result.exit_code == 1
                assert 'does not match the selected target' in result.output
                assert [row.operation for row in management.operations] == ['status']

        asyncio.run(scenario())


class TestPrepare:
    @pytest.mark.parametrize('via_symlink', [False, True], ids=['direct', 'symlink'])
    @pytest.mark.parametrize('operation', ['prepare', 'catalog'])
    def test_keeps_non_report_artifacts_out_of_the_reports_directory(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        operation: str,
        via_symlink: bool,
    ) -> None:
        reports = tmp_path / 'reports'
        reports.mkdir()
        monkeypatch.setattr(commands, 'REPORTS_DIR', reports)
        directory = reports

        if via_symlink:
            directory = tmp_path / 'alias'
            directory.symlink_to(reports, target_is_directory=True)

        output = directory / 'artifact.json'
        source = tmp_path / 'missing-input.json'
        arguments = (
            ['prepare', str(source), str(output)]
            if operation == 'prepare'
            else ['catalog', str(output), str(source)]
        )

        result = runner.invoke(commands.app, arguments)

        assert result.exit_code == 1
        assert 'Only `bench close` writes under reports/.' in result.output
        assert not output.exists()


class TestPublish:
    def test_refuses_an_invalid_reviewed_artifact_before_connecting(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / 'release.json'
        payload = make_release(cases=2).model_dump(mode='json')
        payload['unexpected'] = 'private-invalid-input'
        path.write_text(json.dumps(payload))

        def refuse_connection(name: str) -> None:
            pytest.fail(f'Invalid artifact contacted target {name}.')

        monkeypatch.setattr(commands, '_connection', refuse_connection)

        result = runner.invoke(commands.app, ['publish', 'production', str(path)])

        assert result.exit_code == 1
        assert 'Extra inputs are not permitted' in result.output


class TestExportEvidence:
    def test_refuses_to_overwrite_a_saved_cut_before_connecting(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / 'existing.json'
        path.write_text('previous immutable capture')

        def refuse_connection(name: str) -> None:
            pytest.fail(f'Existing evidence contacted target {name}.')

        monkeypatch.setattr(commands, '_connection', refuse_connection)

        result = runner.invoke(
            commands.app, ['export', 'production', 'writing', str(path)]
        )

        assert result.exit_code == 1
        assert 'evidence output already exists' in result.output
        assert path.read_text() == 'previous immutable capture'

    def test_exports_a_complete_private_cut_and_refreshes_its_actual_fit(
        self,
        runtime: D1Runtime,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def scenario() -> None:
            async with native_management(runtime) as management:
                use_management(monkeypatch, management)
                release = make_release(cases=2, policy=load_policy())
                await publish_release(management, release)
                await vote_capture(management, release)
                path = tmp_path / 'evidence.json'

                exported = await asyncio.to_thread(
                    runner.invoke,
                    commands.app,
                    ['export', 'production', 'writing', str(path)],
                )
                assert exported.exit_code == 0, exported.output
                capture = read_export(path)
                snapshot = analyze_export(capture)

                assert snapshot.analysis.accepted == 1
                assert S_IMODE(path.stat().st_mode) == 0o600
                assert len(capture.sections) == 13
                refreshed = await asyncio.to_thread(
                    runner.invoke, commands.app, ['refresh', 'production', str(path)]
                )
                assert refreshed.exit_code == 0, refreshed.output
                current = await management.ledger.read.family('writing')
                assert current is not None and current.point_id == snapshot.points.id
                repeated = await asyncio.to_thread(
                    runner.invoke, commands.app, ['refresh', 'production', str(path)]
                )
                assert repeated.exit_code == 0, repeated.output
                assert await management.ledger.read.family('writing') == current

                status = await asyncio.to_thread(
                    runner.invoke, commands.app, ['status', 'production', 'writing']
                )
                assert status.exit_code == 0, status.output
                assert 'Sampling points activated at revision' in refreshed.output
                assert 'fitted sampling points are already active' in repeated.output
                assert capture.header.id in status.output

        asyncio.run(scenario())


class TestModerate:
    def test_replays_the_exact_reviewed_plan_after_a_committed_reply_is_lost(
        self,
        runtime: D1Runtime,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def scenario() -> None:
            async with native_management(runtime) as management:
                use_management(monkeypatch, management)
                release = make_release(cases=2, policy=load_policy())
                await publish_release(management, release)
                capture = await vote_capture(management, release)
                receipt = Receipt.model_validate_json(
                    canonical(capture.rows('receipts')[0])
                )
                claims = await management.ledger.read.claims('writing', 'owner')
                claim = next(row for row in claims.rows if row.id == 'claim')
                plan = Moderation(
                    id='reviewed-plan',
                    family='writing',
                    principal=command.PRINCIPAL,
                    action='exclude',
                    reason='Synthetic abuse investigation.',
                    created_at=management.now,
                    targets=(
                        Target(receipt_seq=receipt.seq, claim_version=claim.version),
                    ),
                )
                path = tmp_path / 'moderation.json'
                path.write_text(plan.model_dump_json())
                management.lose_reply = 'moderate'

                failed = await asyncio.to_thread(
                    runner.invoke, commands.app, ['moderate', 'production', str(path)]
                )
                assert failed.exit_code == 1
                assert 'response was lost' in failed.output

                before = await management.ledger.read.family('writing')
                retried = await asyncio.to_thread(
                    runner.invoke, commands.app, ['moderate', 'production', str(path)]
                )
                assert retried.exit_code == 0, retried.output

                assert await management.ledger.read.family('writing') == before
                chunks = [
                    row
                    for row in management.operations
                    if isinstance(row, command.Moderate)
                ]
                assert len(chunks) == 2 and chunks[0] == chunks[1]
                status = await management.ledger.investigation.status('writing')
                assert status is not None and status.eligible_votes == 0

        asyncio.run(scenario())


class TestCatalog:
    def test_writes_only_approved_public_family_labels(self, tmp_path: Path) -> None:
        release = make_release(family='synthetic-public', cases=2)
        path = tmp_path / 'release.json'
        path.write_text(release.model_dump_json())
        output = tmp_path / 'catalog.json'

        result = runner.invoke(commands.app, ['catalog', str(output), str(path)])

        assert result.exit_code == 0, result.output
        assert json.loads(output.read_text()) == [
            {
                'name': 'synthetic-public',
                'aspect': None,
                'display_name': 'synthetic-public 1',
                'defects': ['tone'],
            }
        ]
