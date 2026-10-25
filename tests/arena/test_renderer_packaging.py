import os
import shutil
import stat
import subprocess
import sys
import tomllib
from email.parser import BytesParser
from pathlib import Path

import pytest

from assistant_bench.arena.runtime import load_target, renderer_config
from assistant_bench.paths import REPO_ROOT
from assistant_bench_arena.runtime_models import Profile


def bundle(
    project: Path, configuration: Path, output: Path, environment: dict[str, str]
) -> None:
    with output.with_suffix('.log').open('w') as log:
        result = subprocess.run(
            [
                str(Path(sys.executable).with_name('pywrangler')),
                'deploy',
                '--dry-run',
                '--config',
                str(configuration),
                '--env-file',
                '/dev/null',
                '--outdir',
                str(output),
            ],
            cwd=project,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=180,
        )

    assert result.returncode == 0, f'Native bundle failed. See {log.name}.'


class TestRendererConfig:
    def test_bundles_exact_locked_dependencies_from_the_generated_descriptor(
        self, tmp_path: Path
    ) -> None:
        project = tmp_path / 'project'
        shutil.copytree(REPO_ROOT / 'src', project / 'src')
        for name in ('pyproject.toml', 'pylock.toml', 'wrangler.toml'):
            shutil.copyfile(REPO_ROOT / name, project / name)
        (project / 'node_modules').symlink_to(REPO_ROOT / 'node_modules')
        preserved = {
            path: path.read_bytes()
            for path in project.rglob('*')
            if path.is_file() and 'node_modules' not in path.parts
        }
        lock = tomllib.loads((project / 'pylock.toml').read_text())
        versions = {package['name']: package['version'] for package in lock['packages']}
        target = load_target('staging', manifest_path=project / 'wrangler.toml')
        target = target.model_copy(
            update={
                'worker_name': 'synthetic-render-packaging',
                'account_id': 'synthetic-account',
                'profile': Profile(
                    namespace='synthetic-render-packaging',
                    app_origin='https://app.example.com',
                    render_origin='https://render.example.com',
                ),
            }
        )
        environment = {
            name: os.environ[name]
            for name in (
                'HOME',
                'LANG',
                'PATH',
                'TMPDIR',
                'SSL_CERT_FILE',
                'NIX_SSL_CERT_FILE',
                'NODE_EXTRA_CA_CERTS',
            )
            if name in os.environ
        }
        environment.update(
            PATH=os.pathsep.join(
                [str(REPO_ROOT / 'node_modules' / '.bin'), environment['PATH']]
            ),
            WRANGLER_SEND_METRICS='false',
            XDG_CONFIG_HOME=str(tmp_path / 'config'),
        )
        output = tmp_path / 'bundled'

        with renderer_config(target) as configuration:
            permissions = stat.S_IMODE(configuration.stat().st_mode)
            owner = configuration.stat().st_uid
            bundle(project, configuration, output, environment)
            misplaced = tmp_path / 'misplaced.toml'
            misplaced.write_bytes(configuration.read_bytes())
            bundle(project, misplaced, tmp_path / 'misplaced', environment)

        assert not configuration.exists()
        vendor = project / 'python_modules'
        bundled = output / 'python_modules'
        for package, module in (
            ('pydantic', 'pydantic'),
            ('workers-runtime-sdk', 'workers'),
        ):
            relative = Path(module) / '__init__.py'
            assert (bundled / relative).read_bytes() == (vendor / relative).read_bytes()
            metadata = Path(
                f'{package.replace("-", "_")}-{versions[package]}.dist-info/METADATA'
            )
            assert (bundled / metadata).read_bytes() == (vendor / metadata).read_bytes()
            installed = BytesParser().parsebytes((bundled / metadata).read_bytes())
            assert installed['Version'] == versions[package]
            assert not (tmp_path / 'misplaced' / 'python_modules' / relative).exists()
        version = Path('pydantic/version.py')
        assert (bundled / version).read_bytes() == (vendor / version).read_bytes()
        core = next((vendor / 'pydantic_core').glob('_pydantic_core*.so'))
        assert core.read_bytes().startswith(b'\0asm')
        assert (bundled / core.relative_to(vendor)).read_bytes() == core.read_bytes()
        assert (output / 'render.py').read_bytes() == (
            project / 'src/render.py'
        ).read_bytes()
        assert permissions == 0o600
        assert owner == os.getuid()
        assert {path: path.read_bytes() for path in preserved} == preserved

    def test_removes_its_private_descriptor_after_a_failed_operation(
        self, tmp_path: Path
    ) -> None:
        manifest = tmp_path / 'wrangler.toml'
        shutil.copyfile(REPO_ROOT / 'wrangler.toml', manifest)
        target = load_target('staging', manifest_path=manifest)

        with (
            pytest.raises(RuntimeError, match='Synthetic deployment failure'),
            renderer_config(target) as configuration,
        ):
            raise RuntimeError('Synthetic deployment failure')

        assert not configuration.exists()
        assert manifest.read_bytes() == (REPO_ROOT / 'wrangler.toml').read_bytes()
