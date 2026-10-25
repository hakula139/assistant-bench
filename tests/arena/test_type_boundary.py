import json
import subprocess
import sys
import tomllib
from pathlib import Path
from textwrap import dedent

from pydantic import BaseModel

from assistant_bench.paths import REPO_ROOT


class Diagnostic(BaseModel):
    file: str
    rule: str | None = None


class Diagnostics(BaseModel):
    generalDiagnostics: list[Diagnostic]


class TestNativeTypePolicy:
    def test_only_worker_sources_allow_runtime_provided_modules(
        self, tmp_path: Path
    ) -> None:
        configuration = tomllib.loads((REPO_ROOT / 'pyproject.toml').read_text())[
            'tool'
        ]['basedpyright']
        probes = (
            'src/assistant_bench/probe.py',
            'arena/src/probe.py',
            'arena/src/assistant_bench_arena/probe.py',
            'arena/src/assistant_bench_arena/worker/probe.py',
        )
        for name in probes:
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                dedent("""
                    from js import Headers
                    import missing_runtime_dependency

                    headers = Headers.new()
                    invalid_headers = Headers.new(1)
                    dependency = missing_runtime_dependency
                """)
            )
        configuration['include'] = list(probes)
        configuration['extraPaths'] = [
            str(REPO_ROOT / path) for path in configuration['extraPaths']
        ]
        project = tmp_path / 'pyrightconfig.json'
        project.write_text(json.dumps(configuration))

        result = subprocess.run(
            [
                sys.executable,
                '-m',
                'basedpyright',
                '--project',
                str(project),
                '--pythonpath',
                sys.executable,
                '--outputjson',
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 1, result.stdout + result.stderr
        diagnostics = Diagnostics.model_validate_json(result.stdout)

        for name in probes:
            rules = {
                diagnostic.rule
                for diagnostic in diagnostics.generalDiagnostics
                if Path(diagnostic.file) == tmp_path / name
            }
            assert 'reportMissingImports' in rules, name
            assert 'reportCallIssue' in rules, name
            assert ('reportMissingModuleSource' in rules) == ('/worker/' not in name), (
                name,
                rules,
            )
