"""Revision-owned versions of the bench's generation behavior."""

import tomllib
from functools import cache
from importlib import metadata

from pydantic import BaseModel, ConfigDict, Field

from assistant_bench.paths import REPO_ROOT


class Generation(BaseModel):
    """The revision's generation protocols and locked native package versions."""

    model_config = ConfigDict(frozen=True)

    protocol: int = Field(gt=0)
    harbor: str
    messages_protocol: int = Field(gt=0)
    anthropic_api_version: str
    anthropic: str

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
    """Read a revision's declared protocol and locked native package versions."""
    config = tomllib.loads(project)['tool']['assistant-bench']
    packages = {
        package['name']: package['version']
        for package in tomllib.loads(lock)['package']
    }
    return Generation(
        protocol=config['generation-protocol'],
        harbor=packages['harbor'],
        messages_protocol=config['messages-protocol'],
        anthropic_api_version=config['anthropic-api-version'],
        anthropic=packages['anthropic'],
    )


@cache
def load_generation() -> Generation:
    """Resolve current versions, refusing an environment outside its package lock."""
    generation = parse_generation(
        (REPO_ROOT / 'pyproject.toml').read_text(),
        (REPO_ROOT / 'uv.lock').read_text(),
    )
    for name, locked in (
        ('Harbor', generation.harbor),
        ('Anthropic', generation.anthropic),
    ):
        if metadata.version(name.lower()) != locked:
            raise ValueError(f'Installed {name} differs from uv.lock. Run `uv sync`.')
    return generation
