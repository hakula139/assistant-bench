from importlib import metadata
from pathlib import Path

import pytest

from assistant_bench import generation
from assistant_bench.generation import Generation, load_generation, parse_generation


PROJECT = """\
[project]
version = "7.0.0"

[tool.assistant-bench]
generation-protocol = 2
"""
LOCK = """\
[[package]]
name = "another-package"
version = "9.0.0"

[[package]]
name = "harbor"
version = "0.24.0"
"""


class TestGeneration:
    class TestVersion:
        @pytest.mark.parametrize(
            ('harness', 'expected'),
            [('generate', '2'), ('terminus-2', '0.24.0+bench.2')],
        )
        def test_binds_native_agent_behavior_to_the_locked_harbor_version(
            self, harness: str, expected: str
        ) -> None:
            versions = Generation(protocol=2, harbor='0.24.0')

            version = versions.version(harness)

            assert version == expected


class TestParseGeneration:
    def test_uses_the_declared_protocol_and_the_named_harbor_package(self) -> None:
        versions = parse_generation(PROJECT, LOCK)

        assert versions == Generation(protocol=2, harbor='0.24.0')


class TestLoadGeneration:
    @pytest.mark.parametrize('installed', ['0.24.0', '0.25.0'])
    def test_requires_the_installed_native_agent_to_match_the_lock(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, installed: str
    ) -> None:
        (tmp_path / 'pyproject.toml').write_text(PROJECT)
        (tmp_path / 'uv.lock').write_text(LOCK)
        monkeypatch.setattr(generation, 'REPO_ROOT', tmp_path)

        def version(name: str) -> str:
            return installed

        monkeypatch.setattr(metadata, 'version', version)

        if installed == '0.24.0':
            assert load_generation() == Generation(protocol=2, harbor=installed)
        else:
            with pytest.raises(
                ValueError, match=r'Harbor differs from uv.lock.*uv sync'
            ):
                load_generation()
