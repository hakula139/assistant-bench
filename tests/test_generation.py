from collections.abc import Iterator
from importlib import metadata
from pathlib import Path

import pytest
from pydantic import ValidationError

from assistant_bench import generation
from assistant_bench.generation import Generation, load_generation, parse_generation
from support import make_generation


PROJECT = """\
[project]
version = "7.0.0"

[tool.assistant-bench]
generation-protocol = 2
messages-protocol = 1
anthropic-api-version = "2023-06-01"
"""
LOCK = """\
[[package]]
name = "another-package"
version = "9.0.0"

[[package]]
name = "anthropic"
version = "2.3.0"

[[package]]
name = "harbor"
version = "0.24.0"

[[package]]
name = "openai"
version = "3.1.0"
"""
LOCKED = {'harbor': '0.24.0', 'anthropic': '2.3.0', 'openai': '3.1.0'}


class TestGeneration:
    class TestVersion:
        def test_requires_the_native_identity_metadata(self) -> None:
            with pytest.raises(ValidationError):
                Generation.model_validate({'protocol': 2, 'harbor': '0.24.0'})

        @pytest.mark.parametrize(
            ('harness', 'base'), [('generate', '2'), ('terminus-2', '0.24.0+bench.2')]
        )
        def test_includes_native_versions_only_for_the_messages_api(
            self, harness: str, base: str
        ) -> None:
            versions = make_generation(protocol=2, harbor='0.24.0')

            assert (
                versions.version(harness, 'anthropic-messages')
                == f'{base}+messages.1.2023-06-01'
            )
            assert versions.version(harness, 'openai-responses') == base
            assert versions.version(harness, 'openai-chat-completions') == base

        @pytest.mark.parametrize(
            ('harness', 'expected'),
            [('generate', '2'), ('terminus-2', '0.24.0+bench.2')],
        )
        def test_binds_native_agent_behavior_to_the_locked_harbor_version(
            self, harness: str, expected: str
        ) -> None:
            versions = make_generation(protocol=2, harbor='0.24.0')

            version = versions.version(harness)

            assert version == expected


class TestParseGeneration:
    def test_uses_the_declared_protocol_and_the_named_packages(self) -> None:
        versions = parse_generation(PROJECT, LOCK)

        assert versions == make_generation(protocol=2, harbor='0.24.0')

    @pytest.mark.parametrize(
        ('package', 'changes'),
        [
            pytest.param('harbor', True, id='harbor-supplies-terminus'),
            pytest.param('anthropic', False, id='anthropic-sdk'),
            pytest.param('openai', False, id='openai-sdk'),
        ],
    )
    def test_reads_only_the_locked_harbor_version(
        self, package: str, changes: bool
    ) -> None:
        locked = f'name = "{package}"\nversion = "{LOCKED[package]}"'
        bumped = LOCK.replace(locked, f'name = "{package}"\nversion = "99.0.0"')
        assert bumped != LOCK

        versions = parse_generation(PROJECT, bumped)

        assert (versions != parse_generation(PROJECT, LOCK)) is changes


class TestLoadGeneration:
    @pytest.fixture(autouse=True)
    def clear_cache(self) -> Iterator[None]:
        load_generation.cache_clear()
        yield
        load_generation.cache_clear()

    def test_reuses_one_resolution_per_process(self) -> None:
        assert load_generation() is load_generation()

    def test_requires_the_installed_harbor_to_match_the_lock(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / 'pyproject.toml').write_text(PROJECT)
        (tmp_path / 'uv.lock').write_text(LOCK)
        monkeypatch.setattr(generation, 'REPO_ROOT', tmp_path)

        def version(name: str) -> str:
            return '0.25.0' if name == 'harbor' else LOCKED[name]

        monkeypatch.setattr(metadata, 'version', version)

        with pytest.raises(ValueError, match=r'Harbor differs from uv.lock.*uv sync'):
            load_generation()
