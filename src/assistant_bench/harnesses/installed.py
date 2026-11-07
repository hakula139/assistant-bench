"""Pinned vendor CLIs with host-only endpoint credentials and trial labels."""

import asyncio
import hashlib
import os
import shlex
from collections import ChainMap
from collections.abc import Mapping
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any, ClassVar, override

from harbor.agents.agent_auth_resolution import ResolvedModelConnection
from harbor.agents.installed.claude_code import (
    ClaudeCode as HarborClaudeCode,
    ClaudeCodeOptions as HarborClaudeCodeOptions,
)
from harbor.agents.installed.codex import (
    Codex as HarborCodex,
    CodexOptions as HarborCodexOptions,
)
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from pydantic import BaseModel, Field, field_validator

from assistant_bench.catalog import load_export
from assistant_bench.harnesses import cache, proxy
from assistant_bench.harnesses.endpoint import Endpoint, preflight
from assistant_bench.harnesses.labels import Labels
from assistant_bench.harnesses.usage import append_attempt, record_calls


class PinnedVersion(BaseModel):
    version: str | None = Field(pattern=r'^\d+\.\d+\.\d+$')

    @field_validator('version')
    @classmethod
    def _require_version(cls, value: str | None) -> str:
        if value is None:
            raise ValueError('A pinned CLI version is required')
        return value


class ClaudeCodeOptions(PinnedVersion, HarborClaudeCodeOptions, Labels):
    max_output_tokens: int = Field(gt=0)


class CodexOptions(PinnedVersion, HarborCodexOptions, Labels):
    pass


async def _install(
    environment: BaseEnvironment,
    harness: cache.InstalledHarness,
    version: str,
) -> str:
    result = await environment.exec(command='uname -m')
    if result.return_code or result.stdout is None:
        raise RuntimeError('Could not determine the task container architecture.')
    path = await asyncio.to_thread(
        cache.artifact, harness, version, result.stdout.strip()
    )
    command = 'claude' if harness == 'claude-code' else 'codex'
    destination = f'/usr/local/bin/{command}'
    await environment.upload_file(path, destination)
    result = await environment.exec(
        command=f'chmod 755 {shlex.quote(destination)} && {command} --version',
        user='root',
    )
    if result.return_code or version not in (result.stdout or '').split():
        raise RuntimeError(
            f'The installed `{command}` does not report version `{version}`.'
        )
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ClaudeCode(HarborClaudeCode):
    options_model: ClassVar[type[HarborClaudeCodeOptions]] = ClaudeCodeOptions
    binary_sha256: str

    @classmethod
    @override
    def check_model_credentials(
        cls,
        connection: ResolvedModelConnection,
        *,
        kwargs: dict[str, Any],
        env: Mapping[str, str],
        explicit_env: Mapping[str, str] | None = None,
    ) -> None:
        preflight(env, 'anthropic-messages')

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        assert isinstance(self.options, ClaudeCodeOptions)
        assert self.options.version is not None
        self.binary_sha256 = await _install(
            environment, 'claude-code', self.options.version
        )

    @override
    def _resolve_auth_env(self) -> dict[str, str]:
        assert isinstance(self.options, ClaudeCodeOptions)
        return {
            'ANTHROPIC_BASE_URL': self._extra_env['ANTHROPIC_BASE_URL'],
            'ANTHROPIC_AUTH_TOKEN': self._extra_env['ANTHROPIC_AUTH_TOKEN'],
            'CLAUDE_CODE_MAX_OUTPUT_TOKENS': str(self.options.max_output_tokens),
        }

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        endpoint = Endpoint.read(self._get_env, 'anthropic-messages')
        assert isinstance(self.options, ClaudeCodeOptions)
        started_at = datetime.now(UTC)
        model = self.model_name.removeprefix('anthropic/') if self.model_name else ''
        calls: list[dict[str, Any]] = []
        failed = True
        try:
            async with proxy.connect(
                endpoint, 'anthropic-messages', model
            ) as connection:
                calls = connection.calls
                self._base_url = connection.url
                self._extra_env.update(
                    {
                        'ANTHROPIC_BASE_URL': connection.url,
                        'ANTHROPIC_AUTH_TOKEN': connection.token,
                    }
                )
                await super().run(instruction, environment, context)
            failed = False
        finally:
            await _finish(self, environment, context, started_at, calls, failed=failed)


class Codex(HarborCodex):
    options_model: ClassVar[type[HarborCodexOptions]] = CodexOptions
    binary_sha256: str

    @classmethod
    @override
    def check_model_credentials(
        cls,
        connection: ResolvedModelConnection,
        *,
        kwargs: dict[str, Any],
        env: Mapping[str, str],
        explicit_env: Mapping[str, str] | None = None,
    ) -> None:
        preflight(env, 'openai-responses')

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        assert isinstance(self.options, CodexOptions)
        assert self.options.version is not None
        self.binary_sha256 = await _install(environment, 'codex', self.options.version)

    @override
    def _resolve_auth_json_path(self) -> Path | None:
        return None

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        endpoint = Endpoint.read(self._get_env, 'openai-responses')
        assert isinstance(self.options, CodexOptions)
        started_at = datetime.now(UTC)
        model = self.model_name.removeprefix('openai/') if self.model_name else ''
        calls: list[dict[str, Any]] = []
        failed = True
        try:
            async with proxy.connect(endpoint, 'openai-responses', model) as connection:
                calls = connection.calls
                self._base_url = f'{connection.url}/v1'
                self._extra_env.update(
                    {
                        'OPENAI_BASE_URL': self._base_url,
                        'OPENAI_API_KEY': connection.token,
                    }
                )
                await super().run(instruction, environment, context)
            failed = False
        finally:
            await _finish(self, environment, context, started_at, calls, failed=failed)


async def _finish(
    agent: ClaudeCode | Codex,
    environment: BaseEnvironment,
    context: AgentContext,
    started_at: datetime,
    calls: list[dict[str, Any]],
    *,
    failed: bool,
) -> None:
    try:
        await environment.download_dir(
            agent.environment_logs_dir.as_posix(), agent.logs_dir
        )
        agent.populate_context_post_run(context)
    except Exception:
        if not failed:
            raise
        agent.logger.warning(
            'Native agent logs could not be synchronized after a failed run.'
        )
    finally:
        _record(agent, context, started_at, calls)


def _record(
    agent: ClaudeCode | Codex,
    context: AgentContext,
    started_at: datetime,
    calls: list[dict[str, Any]],
) -> None:
    assert isinstance(agent.options, ClaudeCodeOptions | CodexOptions)
    context.metadata = {
        **(context.metadata or {}),
        'binary_sha256': agent.binary_sha256,
        'nixos_config_rev': load_export(ChainMap(agent.extra_env, os.environ).get).rev,
        'harbor_version': metadata.version('harbor'),
    }
    record_calls(context, calls)
    append_attempt(agent.logs_dir, agent.options, started_at, calls)
