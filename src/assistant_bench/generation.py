"""Revision-owned versions of the bench's generation behavior."""

import tomllib
from importlib import metadata

from pydantic import BaseModel, ConfigDict, Field

from assistant_bench.paths import REPO_ROOT


class Generation(BaseModel):
    """Generation protocol and the Harbor package supplying native agent behavior."""

    model_config = ConfigDict(frozen=True)

    protocol: int = Field(gt=0)
    harbor: str
    messages_protocol: int | None = None
    anthropic_api_version: str | None = None
    anthropic: str | None = None

    def version(self, harness: str, api: str | None = None) -> str:
        if api == 'anthropic-messages':
            return (
                f'{self.version(harness)}+messages.{self.messages_protocol}'
                f'.{self.anthropic_api_version}.{self.anthropic}'
            )
        if harness == 'terminus-2':
            return f'{self.harbor}+bench.{self.protocol}'
        return str(self.protocol)


def parse_generation(project: str, lock: str) -> Generation:
    """Read a revision's declared protocol and locked Harbor package version."""
    config = tomllib.loads(project)
    packages = tomllib.loads(lock)['package']
    return Generation(
        protocol=config['tool']['assistant-bench']['generation-protocol'],
        harbor=next(
            package['version'] for package in packages if package['name'] == 'harbor'
        ),
        messages_protocol=config['tool']['assistant-bench'].get('messages-protocol'),
        anthropic_api_version=config['tool']['assistant-bench'].get(
            'anthropic-api-version'
        ),
        anthropic=next(
            (
                package['version']
                for package in packages
                if package['name'] == 'anthropic'
            ),
            None,
        ),
    )


def load_generation() -> Generation:
    """Resolve current versions, refusing an environment outside its package lock."""
    generation = parse_generation(
        (REPO_ROOT / 'pyproject.toml').read_text(),
        (REPO_ROOT / 'uv.lock').read_text(),
    )
    if metadata.version('harbor') != generation.harbor:
        raise ValueError('Installed Harbor differs from uv.lock. Run `uv sync`.')
    return generation


def generation_version(harness: str, api: str | None = None) -> str:
    generation = load_generation()
    if (
        api == 'anthropic-messages'
        and metadata.version('anthropic') != generation.anthropic
    ):
        raise ValueError('Installed Anthropic differs from uv.lock. Run `uv sync`.')
    return generation.version(harness, api)
