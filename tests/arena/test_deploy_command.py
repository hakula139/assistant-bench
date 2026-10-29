from pathlib import Path

import pytest
from typer.testing import CliRunner

from assistant_bench.arena import commands
from assistant_bench.arena.d1 import TargetName
from assistant_bench.arena.runtime import RuntimeTarget
from assistant_bench_arena.runtime_models import Profile


runner = CliRunner()


@pytest.fixture
def target(tmp_path: Path) -> RuntimeTarget:
    return RuntimeTarget(
        name='production',
        manifest_path=tmp_path / 'wrangler.toml',
        worker_name='synthetic-owner',
        compatibility_date='2026-10-24',
        account_id='synthetic-account',
        database_id='synthetic-database',
        profile=Profile(
            namespace='synthetic',
            app_origin='https://app.example.com',
            render_origin='https://render.example.com',
        ),
    )


class TestDeployCommand:
    @pytest.mark.parametrize('name', ['staging', 'production'])
    def test_dispatches_the_exact_selected_target(
        self, target: RuntimeTarget, monkeypatch: pytest.MonkeyPatch, name: TargetName
    ) -> None:
        selected = target.model_copy(update={'name': name})
        received: list[RuntimeTarget] = []

        def load(requested: TargetName) -> RuntimeTarget:
            assert requested == name
            return selected

        monkeypatch.setattr(commands, 'load_target', load)
        monkeypatch.setattr(commands, 'deploy_target', received.append)

        result = runner.invoke(commands.app, ['deploy', name])

        assert result.exit_code == 0, result.output
        assert received == [selected]
        assert result.stdout == f'Deployed voting services for `{name}`.\n'

    def test_rejects_an_unknown_target_before_loading_or_deploying(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def unexpected(name: TargetName) -> RuntimeTarget:
            pytest.fail('An unknown target reached the native loader.')

        monkeypatch.setattr(commands, 'load_target', unexpected)

        result = runner.invoke(commands.app, ['deploy', 'unknown'])

        assert result.exit_code == 1
        assert 'staging' in result.stderr and 'production' in result.stderr

    @pytest.mark.parametrize(
        'error', [ValueError('Invalid target.'), OSError('Cannot read target.')]
    )
    def test_surfaces_deployment_failure_without_a_success_message(
        self, target: RuntimeTarget, monkeypatch: pytest.MonkeyPatch, error: Exception
    ) -> None:
        def fail(selected: RuntimeTarget) -> None:
            assert selected is target
            raise error

        def load(name: TargetName) -> RuntimeTarget:
            return target

        monkeypatch.setattr(commands, 'load_target', load)
        monkeypatch.setattr(commands, 'deploy_target', fail)

        result = runner.invoke(commands.app, ['deploy', 'production'])

        assert result.exit_code == 1
        assert result.stderr == f'{error}\n'
        assert 'Deployed' not in result.stdout
