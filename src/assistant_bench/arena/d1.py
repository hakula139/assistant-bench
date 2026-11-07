"""The arena's D1 targets and their credential sources."""

import os
import tomllib
from collections.abc import Generator
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from typing import Literal

import httpx
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict

from assistant_bench import database
from assistant_bench.messages import InputError
from assistant_bench.paths import REPO_ROOT


WRANGLER_CONFIG = REPO_ROOT / 'web' / 'wrangler.toml'
TOKEN_FILE_VAR = 'BENCH_D1_TOKEN_FILE'
ENV_FILE_VAR = 'BENCH_D1_ENV_FILE'
TargetName = Literal['staging', 'production', 'private']


# ── Targets ──


class _Binding(BaseModel):
    model_config = ConfigDict(extra='allow')

    binding: str
    database_name: str
    database_id: str | None = None


class _Environment(BaseModel):
    model_config = ConfigDict(extra='allow')

    d1_databases: list[_Binding] = []


class _Config(BaseModel):
    model_config = ConfigDict(extra='allow')

    account_id: str
    env: dict[str, _Environment] = {}


@contextmanager
def connect(
    target: TargetName, *, credential: str | None = None
) -> Generator[database.Database]:
    """Open the target's database with the token in the file `.env` names.

    Raises
    ------
    InputError
        `web/wrangler.toml` records no database ID for the target, or `.env`
        names conflicting credential sources or no readable token.
    """
    config = _config()
    databases = [
        binding
        for binding in config.env.get(target, _Environment()).d1_databases
        if binding.binding == 'DB'
    ]
    if len(databases) != 1:
        raise InputError(
            f'Target `{target}` needs one `DB` binding in `web/wrangler.toml`.'
        )

    binding = databases[0]
    if binding.database_id is None:
        raise InputError(
            f'Target `{target}` has no D1 database ID in `web/wrangler.toml`. '
            f'Create it with `wrangler d1 create {binding.database_name}` and '
            'record its ID there.'
        )

    credential = credential if credential is not None else token()
    with httpx.Client(
        base_url=database.API_BASE_URL,
        headers={'Authorization': f'Bearer {credential}'},
        timeout=database.TIMEOUT_SEC,
    ) as client:
        yield database.RestDatabase(client, config.account_id, binding.database_id)


def token() -> str:
    """Read the configured file-backed D1 credential without retaining it."""
    sources = [
        (name, path)
        for name in (TOKEN_FILE_VAR, ENV_FILE_VAR)
        if (path := os.environ.get(name))
    ]
    if len(sources) != 1:
        raise InputError(
            f'Set exactly one of `{TOKEN_FILE_VAR}` and `{ENV_FILE_VAR}` in '
            '`.env` to the path of a Cloudflare API token file or a dotenv '
            'file containing `CLOUDFLARE_API_TOKEN` with D1 Edit.'
        )

    ((name, path),) = sources
    try:
        text = Path(path).read_text()
    except OSError as error:
        raise InputError(
            f'`{name}` names `{path}`, which cannot be read: {error.strerror}.'
        ) from error

    token = (
        dotenv_values(stream=StringIO(text), interpolate=False).get(
            'CLOUDFLARE_API_TOKEN'
        )
        if name == ENV_FILE_VAR
        else text
    )
    if not token or not token.strip():
        expected = (
            'a nonempty `CLOUDFLARE_API_TOKEN`'
            if name == ENV_FILE_VAR
            else 'a nonempty Cloudflare API token'
        )
        raise InputError(f'`{name}` names `{path}`, which needs {expected}.')

    token = token.strip()
    if any(not ' ' <= character <= '~' for character in token):
        raise InputError(
            f'`{name}` names `{path}`, whose token contains characters that '
            'cannot be used in an HTTP header.'
        )

    return token


def _config() -> _Config:
    return _Config.model_validate(tomllib.loads(WRANGLER_CONFIG.read_text()))
