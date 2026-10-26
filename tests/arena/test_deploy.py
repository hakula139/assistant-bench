import json
import subprocess
import tomllib
from pathlib import Path
from textwrap import dedent

import pytest
from pydantic import JsonValue

from assistant_bench.arena import runtime
from assistant_bench.arena.runtime import RuntimeTarget


_FAMILY: dict[str, JsonValue] = {
    'name': 'writing',
    'display_name': 'Synthetic writing',
    'aspect': None,
    'defects': [],
}


@pytest.fixture
def target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RuntimeTarget:
    source = tmp_path / 'src'
    source.mkdir()
    for name in ('app.py', 'render.py'):
        (source / name).write_text('from workers import WorkerEntrypoint\n')
    (tmp_path / 'arena').mkdir()
    (tmp_path / 'arena' / 'rpc.ts').write_text('export {};\n')
    assets = tmp_path / 'assets'
    assets.mkdir()
    (assets / 'arena-families.json').write_text(json.dumps([_FAMILY]))
    manifest = tmp_path / 'wrangler.toml'
    manifest.write_text(
        dedent("""
            main = "src/app.py"
            compatibility_date = "2026-10-24"
            account_id = "root-account"

            [env.production]
            name = "synthetic-owner"
            account_id = "selected-account"
            compatibility_date = "2026-10-25"
            d1_databases = [{binding = "DB", database_id = "synthetic-database"}]

            [env.production.vars]
            ARENA_NAMESPACE = "synthetic"
            APP_ORIGIN = "https://app.example.com"
            RENDER_ORIGIN = "https://render.example.com"
            TURNSTILE_SITEKEY = "synthetic-sitekey"

            [env.production.assets]
            binding = "ASSETS"
            directory = "assets"
            run_worker_first = true
        """)
    )
    monkeypatch.setenv('CLOUDFLARE_API_TOKEN', 'synthetic-credential')
    return runtime.load_target('production', manifest_path=manifest)


class TestRouting:
    def test_routes_only_the_vote_page_and_api_to_the_native_owner(self) -> None:
        manifest = tomllib.loads(runtime.CONFIG.read_text())

        for name in ('staging', 'production'):
            assets = manifest['env'][name]['assets']
            assert assets['run_worker_first'] == ['/api/*', '/vote', '/vote/']


class TestDeploy:
    def test_checks_all_native_bundles_before_deploying_the_same_descriptors(
        self, target: RuntimeTarget, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        received: list[tuple[str, bool, Path]] = []
        monkeypatch.setenv('BENCH_API_KEY', 'synthetic-unrelated')
        monkeypatch.setenv('SESSION_KEYS', 'synthetic-unrelated')

        def run(
            arguments: list[str], *, cwd: Path, env: dict[str, str], check: bool
        ) -> subprocess.CompletedProcess[str]:
            assert cwd == target.manifest_path.parent
            assert check
            assert env['CLOUDFLARE_ACCOUNT_ID'] == 'selected-account'
            assert 'BENCH_API_KEY' not in env and 'SESSION_KEYS' not in env
            assert env['WRANGLER_SEND_METRICS'] == 'false'
            path = Path(arguments[arguments.index('--config') + 1])
            configuration = tomllib.loads(path.read_text())
            if path == target.manifest_path:
                name = configuration['env']['production']['name']
                assert arguments[arguments.index('--env') + 1] == 'production'
            else:
                name = configuration['name']
                assert configuration['account_id'] == 'selected-account'
                assert configuration['compatibility_date'] == '2026-10-25'
                assert '--env' not in arguments
            assert arguments[arguments.index('--env-file') + 1] == '/dev/null'
            received.append((str(name), '--dry-run' in arguments, path))
            return subprocess.CompletedProcess(arguments, 0)

        monkeypatch.setattr(subprocess, 'run', run)

        runtime.deploy(target)

        assert [(name, dry) for name, dry, _ in received] == [
            (name, dry)
            for dry in (True, False)
            for name in (
                'synthetic-owner',
                'synthetic-owner-rpc',
                'synthetic-owner-render',
            )
        ]
        assert [path for _, _, path in received[:3]] == [
            path for _, _, path in received[3:]
        ]
        assert all(
            not path.exists() for _, _, path in received if path != target.manifest_path
        )

    @pytest.mark.parametrize(
        ('change', 'message'),
        [
            ('missing-main', 'native app entrypoint'),
            ('wrong-main', 'native app entrypoint'),
            ('missing-app', 'owner, renderer and facade'),
            ('missing-renderer', 'owner, renderer and facade'),
            ('missing-facade', 'owner, renderer and facade'),
            ('missing-assets', 'prepared site assets'),
            ('missing-sitekey', 'configured challenge sitekey'),
            ('empty-catalog', 'unique public families'),
            ('duplicate-catalog', 'unique public families'),
            ('oversized-catalog', 'catalog exceeds its limit'),
        ],
    )
    def test_refuses_invalid_inputs_before_any_subprocess(
        self,
        target: RuntimeTarget,
        monkeypatch: pytest.MonkeyPatch,
        change: str,
        message: str,
    ) -> None:
        manifest = target.manifest_path
        content = manifest.read_text()
        catalog = manifest.parent / 'assets' / 'arena-families.json'
        if change == 'missing-main':
            content = content.replace('main = "src/app.py"', '')
        elif change == 'wrong-main':
            content = content.replace('main = "src/app.py"', 'main = "src/other.py"')
        elif change in {
            'missing-app',
            'missing-renderer',
            'missing-facade',
        }:
            relative = {
                'missing-app': 'src/app.py',
                'missing-renderer': 'src/render.py',
                'missing-facade': 'arena/rpc.ts',
            }[change]
            (manifest.parent / relative).unlink()
        elif change == 'missing-assets':
            content = content.split('[env.production.assets]')[0]
        elif change == 'missing-sitekey':
            content = content.replace('TURNSTILE_SITEKEY = "synthetic-sitekey"', '')
        elif change == 'empty-catalog':
            catalog.write_text('[]')
        elif change == 'duplicate-catalog':
            catalog.write_text(json.dumps([_FAMILY, _FAMILY]))
        else:
            catalog.write_text('x' * 1_048_577)
        manifest.write_text(content)

        def unexpected(
            arguments: list[str], *, cwd: Path, env: dict[str, str], check: bool
        ) -> subprocess.CompletedProcess[str]:
            pytest.fail('Invalid deployment inputs reached a native subprocess.')

        monkeypatch.setattr(subprocess, 'run', unexpected)

        with pytest.raises(ValueError, match=message):
            runtime.deploy(target)

    @pytest.mark.parametrize(
        'failed_bundle',
        ['synthetic-owner', 'synthetic-owner-rpc', 'synthetic-owner-render'],
    )
    def test_stops_a_failed_dry_run_before_any_real_deployment(
        self, target: RuntimeTarget, monkeypatch: pytest.MonkeyPatch, failed_bundle: str
    ) -> None:
        received: list[str] = []

        def run(
            arguments: list[str], *, cwd: Path, env: dict[str, str], check: bool
        ) -> subprocess.CompletedProcess[str]:
            assert '--dry-run' in arguments
            path = Path(arguments[arguments.index('--config') + 1])
            config = tomllib.loads(path.read_text())
            name = (
                config['env']['production']['name']
                if path == target.manifest_path
                else config['name']
            )
            received.append(str(name))
            if name == failed_bundle:
                raise subprocess.CalledProcessError(1, arguments)
            return subprocess.CompletedProcess(arguments, 0)

        monkeypatch.setattr(subprocess, 'run', run)

        with pytest.raises(ValueError, match='The native voting deployment failed'):
            runtime.deploy(target)

        assert received[-1] == failed_bundle
