"""Revision-owned versions of the bench's generation behavior."""

import tomllib
from functools import cache
from importlib import metadata
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from assistant_bench.messages import InputError
from assistant_bench.paths import REPO_ROOT


class Generation(BaseModel):
    """The revision's generation protocols and locked Harbor version."""

    model_config = ConfigDict(frozen=True)

    protocol: int = Field(gt=0)
    harbor: str
    messages_protocol: int = Field(gt=0)
    anthropic_api_version: str

    def version(self, harbor_loop: bool, api: str | None = None) -> str:
        if api == 'anthropic-messages':
            return (
                f'{self.version(harbor_loop)}+messages.{self.messages_protocol}'
                f'.{self.anthropic_api_version}'
            )
        if harbor_loop:
            return f'{self.harbor}+bench.{self.protocol}'
        return str(self.protocol)


def parse_generation(project: str, lock: str) -> Generation:
    """Read a revision's declared protocols and locked Harbor version."""
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
    )


@cache
def load_generation(root: Path = REPO_ROOT) -> Generation:
    """Resolve current versions, refusing an installed Harbor outside the lock."""
    generation = parse_generation(
        (root / 'pyproject.toml').read_text(),
        (root / 'uv.lock').read_text(),
    )
    if metadata.version('harbor') != generation.harbor:
        raise InputError('Installed Harbor differs from uv.lock. Run `uv sync`.')
    return generation
