"""Named Worker profiles read from their native deployment configuration."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from textwrap import dedent
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from assistant_bench.arena.d1 import account_id
from assistant_bench.paths import REPO_ROOT
from assistant_bench_arena.models import FamilyView, Record
from assistant_bench_arena.runtime_models import Profile


type TargetName = Literal['staging', 'production']

CONFIG = REPO_ROOT / 'wrangler.toml'


class RuntimeTarget(Record):
    name: TargetName
    manifest_path: Path
    worker_name: str
    compatibility_date: str
    account_id: str
    profile: Profile
    database_id: str

    @property
    def facade_name(self) -> str:
        return f'{self.worker_name}-rpc'


class _Database(BaseModel):
    model_config = ConfigDict(extra='allow', strict=True)

    binding: str
    database_id: str


class _Assets(BaseModel):
    model_config = ConfigDict(extra='allow', strict=True)

    directory: str


class _Environment(BaseModel):
    model_config = ConfigDict(extra='allow', strict=True)

    name: str
    compatibility_date: str | None = None
    main: str | None = None

    vars: dict[str, JsonValue]
    d1_databases: list[_Database]
    assets: _Assets | None = None


class _Manifest(BaseModel):
    model_config = ConfigDict(extra='allow', strict=True)

    compatibility_date: str
    main: str | None = None
    env: dict[str, _Environment] = Field(default_factory=dict)


def load_target(name: TargetName, *, manifest_path: Path = CONFIG) -> RuntimeTarget:
    """Resolve an explicitly configured native profile without a target fallback."""
    manifest = _Manifest.model_validate(tomllib.loads(manifest_path.read_text()))
    environment = manifest.env.get(name)

    if environment is None:
        raise ValueError(f'Arena target `{name}` is not configured.')

    databases = [item for item in environment.d1_databases if item.binding == 'DB']

    if len(databases) != 1:
        raise ValueError(f'Arena target `{name}` requires one `DB` binding.')

    values = environment.vars
    profile = Profile.model_validate(
        {
            'namespace': values.get('ARENA_NAMESPACE'),
            'app_origin': values.get('APP_ORIGIN'),
            'render_origin': values.get('RENDER_ORIGIN'),
        }
    )

    return RuntimeTarget(
        name=name,
        manifest_path=manifest_path.resolve(),
        worker_name=environment.name,
        compatibility_date=(
            environment.compatibility_date
            if environment.compatibility_date is not None
            else manifest.compatibility_date
        ),
        account_id=account_id(),
        profile=profile,
        database_id=databases[0].database_id,
    )


def deploy(target: RuntimeTarget) -> None:
    """Deploy the prepared site, sole owner and confined Services with native tools."""
    manifest = _Manifest.model_validate(tomllib.loads(target.manifest_path.read_text()))
    selected = manifest.env[target.name]

    if selected.assets is None:
        raise ValueError('Voting deployment requires its prepared site assets.')

    sitekey = selected.vars.get('TURNSTILE_SITEKEY')

    if not isinstance(sitekey, str) or not sitekey:
        raise ValueError('Voting deployment requires its configured challenge sitekey.')

    directory = target.manifest_path.parent
    main = selected.main if selected.main is not None else manifest.main
    app = directory / 'src' / 'app.py'

    if main is None or (directory / main).resolve() != app.resolve():
        raise ValueError('Voting deployment requires its native app entrypoint.')

    sources = (app, directory / 'src' / 'render.py', directory / 'arena' / 'rpc.ts')

    if any(not source.is_file() for source in sources):
        raise ValueError('Voting deployment requires its owner, renderer and facade.')

    body = (directory / selected.assets.directory / 'arena-families.json').read_bytes()

    if len(body) > 1_048_576:
        raise ValueError('The prepared family catalog exceeds its limit.')

    catalog = TypeAdapter(list[FamilyView]).validate_json(body)

    if not catalog or len({family.name for family in catalog}) != len(catalog):
        raise ValueError('The prepared family catalog requires unique public families.')

    environment = {
        name: os.environ[name]
        for name in (
            'HOME',
            'LANG',
            'NIX_SSL_CERT_FILE',
            'NODE_EXTRA_CA_CERTS',
            'PATH',
            'SSL_CERT_FILE',
            'TMPDIR',
            'XDG_CACHE_HOME',
            'XDG_CONFIG_HOME',
            'CLOUDFLARE_API_TOKEN',
        )
        if name in os.environ
    }

    if not environment.get('CLOUDFLARE_API_TOKEN'):
        raise ValueError('Voting deployment requires its Cloudflare API credential.')

    environment['CLOUDFLARE_ACCOUNT_ID'] = target.account_id
    environment['WRANGLER_SEND_METRICS'] = 'false'
    executable = str(Path(sys.executable).with_name('pywrangler'))

    def run(config: Path, *arguments: str) -> None:
        try:
            subprocess.run(
                [
                    executable,
                    'deploy',
                    '--config',
                    str(config),
                    '--env-file',
                    '/dev/null',
                    *arguments,
                ],
                cwd=directory,
                env=environment,
                check=True,
            )
        except subprocess.CalledProcessError:
            raise ValueError('The native voting deployment failed.') from None

    with facade_config(target) as facade, renderer_config(target) as renderer:
        for arguments in (('--dry-run',), ()):
            run(target.manifest_path, '--env', target.name, *arguments)
            run(facade, *arguments)
            run(renderer, *arguments)


@contextmanager
def management_config(target: RuntimeTarget) -> Generator[Path]:
    """Give the CLI one explicit facade capability without owner bindings."""
    source = dedent(
        f"""
        name = "assistant-bench-management-client"
        compatibility_date = {json.dumps(target.compatibility_date)}
        account_id = {json.dumps(target.account_id)}

        [[services]]
        binding = "MANAGEMENT"
        service = {json.dumps(target.facade_name)}
        entrypoint = "Management"
        remote = true
        """
    ).lstrip()

    with _temporary_config(source) as path:
        yield path


@contextmanager
def facade_config(target: RuntimeTarget) -> Generator[Path]:
    """Configure the fixed native RPC boundary with no database or secrets."""
    parts = [
        dedent(
            f"""
            name = {json.dumps(target.facade_name)}
            main = {json.dumps(str(target.manifest_path.parent / 'arena/rpc.ts'))}
            compatibility_date = {json.dumps(target.compatibility_date)}
            account_id = {json.dumps(target.account_id)}
            workers_dev = false
            preview_urls = false
            routes = []

            [observability]
            enabled = false
            """
        ).lstrip()
    ]
    owners = [
        ('RENDER_OWNER', 'RenderLookup'),
        ('MANAGEMENT_OWNER', 'Management'),
    ]

    for binding, entrypoint in owners:
        parts.append(
            dedent(
                f"""
                [[services]]
                binding = {json.dumps(binding)}
                service = {json.dumps(target.worker_name)}
                entrypoint = {json.dumps(entrypoint)}
                remote = true
                """
            )
        )

    with _temporary_config('\n'.join(parts)) as path:
        yield path


@contextmanager
def renderer_config(target: RuntimeTarget) -> Generator[Path]:
    """Give the separate renderer only its verification key and lookup Service."""
    namespace = int.from_bytes(
        hashlib.sha256(f'{target.profile.namespace}:RENDER_LIMIT'.encode()).digest()[:6]
    )
    source = dedent(
        f'''
        name = {json.dumps(target.worker_name + '-render')}
        main = {json.dumps(str(target.manifest_path.parent / 'src/render.py'))}
        compatibility_date = {json.dumps(target.compatibility_date)}
        account_id = {json.dumps(target.account_id)}
        compatibility_flags = ["python_workers"]
        workers_dev = false
        preview_urls = false

        [[routes]]
        pattern = {json.dumps(target.profile.render_origin.removeprefix('https://'))}
        custom_domain = true

        [observability]
        enabled = false

        [secrets]
        required = ["RENDER_KEYS"]

        [vars]
        ARENA_NAMESPACE = {json.dumps(target.profile.namespace)}
        APP_ORIGIN = {json.dumps(target.profile.app_origin)}
        RENDER_ORIGIN = {json.dumps(target.profile.render_origin)}

        [[ratelimits]]
        name = "RENDER_LIMIT"
        namespace_id = "{namespace}"
        simple = {{ limit = 120, period = 60 }}

        [[services]]
        binding = "LOOKUP"
        service = {json.dumps(target.facade_name)}
        entrypoint = "RenderLookup"
        remote = true
        '''
    ).lstrip()

    # Wrangler discovers Python dependencies beside its configuration file.
    with tempfile.NamedTemporaryFile(
        mode='w',
        prefix='.wrangler-render-',
        suffix='.toml',
        dir=target.manifest_path.parent,
    ) as config:
        config.write(source)
        config.flush()
        yield Path(config.name)


@contextmanager
def _temporary_config(source: str) -> Generator[Path]:
    with tempfile.TemporaryDirectory(prefix='assistant-bench-worker-') as temporary:
        path = Path(temporary) / 'wrangler.toml'
        path.write_text(source)
        yield path
