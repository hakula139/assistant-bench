import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from textwrap import dedent
from zipfile import ZipFile

from assistant_bench.paths import REPO_ROOT


class TestArenaDistribution:
    def test_built_artifacts_preserve_native_sources_and_exclude_generated_content(
        self, tmp_path: Path
    ) -> None:
        package = tmp_path / 'arena'
        source = REPO_ROOT / 'arena'
        shutil.copytree(
            source / 'src',
            package / 'src',
            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'),
        )
        shutil.copyfile(source / 'pyproject.toml', package / 'pyproject.toml')
        for name in (
            'python_modules/generated.py',
            '.venv/native.py',
            'tests/test_control.py',
            '.pytest_cache/generated',
            'src/assistant_bench_arena/worker/__pycache__/generated.pyc',
        ):
            path = package / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('Generated test sentinel.')
        distribution = tmp_path / 'dist'

        built = subprocess.run(
            ['uv', 'build', '--offline', '--out-dir', str(distribution), str(package)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert built.returncode == 0, built.stdout + built.stderr
        wheel = next(distribution.glob('*.whl'))
        archive = next(distribution.glob('*.tar.gz'))

        required = (
            'management_models.py',
            'investigation_models.py',
            'py.typed',
            'schema.sql',
            'worker/__init__.py',
            'worker/api.py',
            'worker/authentication.py',
            'worker/bindings.py',
            'worker/http.py',
            'worker/owner.py',
            'worker/production.py',
            'worker/rendering.py',
            'worker/settings.py',
        )
        with ZipFile(wheel) as installed, tarfile.open(archive) as released:
            root = archive.name.removesuffix('.tar.gz')
            names = installed.namelist() + released.getnames()
            for name in required:
                relative = f'assistant_bench_arena/{name}'
                expected = (source / 'src' / relative).read_bytes()
                assert installed.read(relative) == expected, relative
                archived = released.extractfile(f'{root}/src/{relative}')
                assert archived is not None
                assert archived.read() == expected, relative
            for name in ('app.py', 'render.py'):
                entrypoint = released.extractfile(f'{root}/src/{name}')
                assert entrypoint is not None
                assert entrypoint.read() == (source / 'src' / name).read_bytes()
            assert not any(
                part
                in {'python_modules', '.venv', '__pycache__', '.pytest_cache', 'tests'}
                or part in {'test_control.py', 'test_renderer.py', 'test_coverage.py'}
                for name in names
                for part in Path(name).parts
            ), names

        script = dedent("""
            import sys
            from importlib.resources import files

            sys.path.insert(0, sys.argv[1])
            from assistant_bench_arena import management_models as command

            assert command.__file__.startswith(sys.argv[1])
            assert command.parse_request('{"operation":"status"}') == command.Status()
            assert files('assistant_bench_arena').joinpath('schema.sql').read_bytes()
        """)
        imported = subprocess.run(
            [sys.executable, '-I', '-c', script, str(wheel)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert imported.returncode == 0, imported.stdout + imported.stderr
