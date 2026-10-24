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

    def version(self, harness: str) -> str:
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


def generation_version(harness: str) -> str:
    return load_generation().version(harness)
