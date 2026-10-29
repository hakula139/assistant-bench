import tomllib
from pathlib import Path
from textwrap import dedent

import pytest
from pydantic import ValidationError

from assistant_bench.arena.d1 import TargetName, account_id
from assistant_bench.arena.runtime import (
    facade_config,
    load_target,
    management_config,
    renderer_config,
)


_MANIFEST = dedent("""
    compatibility_date = "2026-10-24"

    [env.staging]
    name = "synthetic-arena"
    d1_databases = [{ binding = "DB", database_id = "synthetic-preview" }]

    [env.staging.vars]
    ARENA_NAMESPACE = "synthetic"
    APP_ORIGIN = "https://app.example.com"
    RENDER_ORIGIN = "https://render.example.com"
""")


class TestLoadTarget:
    @pytest.mark.parametrize('name', ['staging', 'production'])
    def test_resolves_the_named_native_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: TargetName
    ) -> None:
        monkeypatch.chdir(tmp_path)
        path = Path('wrangler.toml')
        path.write_text(_MANIFEST.replace('env.staging', f'env.{name}'))

        target = load_target(name, manifest_path=path)

        assert target.name == name
        assert target.manifest_path == path.resolve()
        assert target.worker_name == 'synthetic-arena'
        assert target.compatibility_date == '2026-10-24'
        assert target.account_id == account_id()
        assert target.database_id == 'synthetic-preview'
        assert target.profile.namespace == 'synthetic'
        assert target.profile.app_origin == 'https://app.example.com'
        assert target.profile.render_origin == 'https://render.example.com'

    def test_rejects_a_missing_target_without_using_root_settings(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / 'wrangler.toml'
        path.write_text(_MANIFEST)

        with pytest.raises(ValueError, match='target `production` is not configured'):
            load_target('production', manifest_path=path)

    @pytest.mark.parametrize(
        ('original', 'replacement', 'field'),
        [
            ('compatibility_date = "2026-10-24"', '', 'compatibility_date'),
            (
                'compatibility_date = "2026-10-24"',
                'compatibility_date = 20261024',
                'compatibility_date',
            ),
        ],
        ids=['missing-date', 'invalid-date'],
    )
    def test_requires_native_deployment_identity(
        self, tmp_path: Path, original: str, replacement: str, field: str
    ) -> None:
        path = tmp_path / 'wrangler.toml'
        path.write_text(_MANIFEST.replace(original, replacement))

        with pytest.raises(ValidationError, match=field):
            load_target('staging', manifest_path=path)

    @pytest.mark.parametrize(
        'bindings',
        [
            '[]',
            '[{ binding = "OTHER", database_id = "other" }]',
            '[{ binding = "DB", database_id = "first" }, '
            '{ binding = "DB", database_id = "second" }]',
        ],
        ids=['absent', 'different-binding', 'duplicate'],
    )
    def test_requires_one_database_with_the_canonical_binding_name(
        self, tmp_path: Path, bindings: str
    ) -> None:
        original = '[{ binding = "DB", database_id = "synthetic-preview" }]'
        path = tmp_path / 'wrangler.toml'
        path.write_text(_MANIFEST.replace(original, bindings))

        with pytest.raises(ValueError, match='requires one `DB` binding'):
            load_target('staging', manifest_path=path)

    def test_selects_the_database_by_binding_with_unrelated_native_settings(
        self, tmp_path: Path
    ) -> None:
        content = _MANIFEST.replace(
            'd1_databases = [',
            'workers_dev = false\n'
            'd1_databases = [{ binding = "OTHER", database_id = "other" }, ',
        )
        path = tmp_path / 'wrangler.toml'
        path.write_text(content)

        target = load_target('staging', manifest_path=path)

        assert target.database_id == 'synthetic-preview'
        assert target.worker_name == 'synthetic-arena'
        assert target.compatibility_date == '2026-10-24'

    def test_resolves_the_environment_compatibility_date(self, tmp_path: Path) -> None:
        path = tmp_path / 'wrangler.toml'
        path.write_text(
            _MANIFEST.replace(
                '[env.staging]', '[env.staging]\ncompatibility_date = "2000-01-01"'
            )
        )

        target = load_target('staging', manifest_path=path)

        assert target.compatibility_date == '2000-01-01'

    @pytest.mark.parametrize(
        ('original', 'replacement'),
        [
            ('ARENA_NAMESPACE = "synthetic"', ''),
            ('APP_ORIGIN = "https://app.example.com"', ''),
            (
                'APP_ORIGIN = "https://app.example.com"',
                'APP_ORIGIN = "https://app.example.com/path"',
            ),
            (
                'APP_ORIGIN = "https://app.example.com"',
                'APP_ORIGIN = "http://127.0.0.1:8874"',
            ),
            (
                'RENDER_ORIGIN = "https://render.example.com"',
                'RENDER_ORIGIN = "https://app.example.com"',
            ),
        ],
        ids=['missing-namespace', 'missing-origin', 'origin-path', 'http', 'same-host'],
    )
    def test_validates_runtime_values_at_the_native_manifest_boundary(
        self, tmp_path: Path, original: str, replacement: str
    ) -> None:
        path = tmp_path / 'wrangler.toml'
        path.write_text(_MANIFEST.replace(original, replacement))

        with pytest.raises(ValidationError):
            load_target('staging', manifest_path=path)


class TestManagementConfig:
    @pytest.mark.parametrize('name', ['staging', 'production'])
    def test_scopes_the_client_to_the_private_facade_service(
        self, tmp_path: Path, name: TargetName
    ) -> None:
        manifest = tmp_path / 'wrangler.toml'
        source = _MANIFEST.replace('env.staging', f'env.{name}')
        manifest.write_text(source)
        target = load_target(name, manifest_path=manifest)

        with management_config(target) as path:
            configuration = tomllib.loads(path.read_text())

            assert path != manifest
            assert configuration == {
                'name': 'assistant-bench-management-client',
                'compatibility_date': '2026-10-24',
                'account_id': account_id(),
                'services': [
                    {
                        'binding': 'MANAGEMENT',
                        'service': 'synthetic-arena-rpc',
                        'entrypoint': 'Management',
                        'remote': True,
                    }
                ],
            }

        assert not path.exists()
        assert manifest.read_text() == source


class TestFacadeConfig:
    def test_exposes_only_the_two_private_owner_capabilities(
        self, tmp_path: Path
    ) -> None:
        manifest = tmp_path / 'wrangler.toml'
        manifest.write_text(_MANIFEST)
        target = load_target('staging', manifest_path=manifest)

        with facade_config(target) as path:
            configuration = tomllib.loads(path.read_text())

            assert configuration == {
                'name': 'synthetic-arena-rpc',
                'main': str(tmp_path / 'arena/rpc.ts'),
                'compatibility_date': '2026-10-24',
                'account_id': account_id(),
                'workers_dev': False,
                'preview_urls': False,
                'routes': [],
                'observability': {'enabled': False},
                'services': [
                    {
                        'binding': binding,
                        'service': 'synthetic-arena',
                        'entrypoint': entrypoint,
                        'remote': True,
                    }
                    for binding, entrypoint in (
                        ('RENDER_OWNER', 'RenderLookup'),
                        ('MANAGEMENT_OWNER', 'Management'),
                    )
                ],
            }


class TestRendererConfig:
    def test_binds_the_renderer_to_only_its_key_and_lookup_facade(
        self, tmp_path: Path
    ) -> None:
        manifest = tmp_path / 'wrangler.toml'
        manifest.write_text(_MANIFEST)
        target = load_target('staging', manifest_path=manifest)

        with renderer_config(target) as path:
            configuration = tomllib.loads(path.read_text())

            assert configuration['main'] == str(tmp_path / 'src/render.py')
            assert configuration['routes'] == [
                {'pattern': 'render.example.com', 'custom_domain': True}
            ]
            assert configuration['secrets'] == {'required': ['RENDER_KEYS']}
            assert configuration['vars'] == {
                'ARENA_NAMESPACE': 'synthetic',
                'APP_ORIGIN': 'https://app.example.com',
                'RENDER_ORIGIN': 'https://render.example.com',
            }
            assert configuration['services'] == [
                {
                    'binding': 'LOOKUP',
                    'service': 'synthetic-arena-rpc',
                    'entrypoint': 'RenderLookup',
                    'remote': True,
                }
            ]
            assert configuration['ratelimits'][0]['name'] == 'RENDER_LIMIT'
            assert 'd1_databases' not in configuration

        assert not path.exists()
