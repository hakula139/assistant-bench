import asyncio
import importlib.metadata
import json
import os
import queue
import re
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from importlib.resources import files
from pathlib import Path
from textwrap import dedent
from typing import Self
from uuid import uuid4

import httpx
from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError

from assistant_bench_arena.ledger import Ledger
from assistant_bench_arena.ledger_models import (
    BootstrapConfiguration,
    Row,
    SqlValue,
    Statement,
)
from assistant_bench_arena.releases import Release
from assistant_bench_arena.schema import bootstrap_statements
from assistant_bench_arena.transport import CommandRejected, Rejection


ROOT = Path(__file__).resolve().parents[1]
_REJECTION = TypeAdapter[Rejection](Rejection.__value__)
_GUARD = re.compile(
    r'^Error: D1_ERROR: ([a-z_]+): SQLITE_CONSTRAINT '
    r'\(extended: SQLITE_CONSTRAINT_TRIGGER\)$'
)

_OWNER = """
    import json

    from pyodide.ffi import JsException
    from workers import Response, WorkerEntrypoint


    class Default(WorkerEntrypoint):
        async def fetch(self, request):
            return Response('ready')


    class LocalManagement(WorkerEntrypoint):
        async def execute(self, payload_json):
            command = json.loads(payload_json)
            statements = [
                self.env.DB.prepare(item['sql']).bind(*item.get('params', []))
                for item in command['statements']
            ]

            try:
                if command['mode'] == 'batch':
                    result = await self.env.DB.batch(statements)
                else:
                    result = await statements[0].all()
            except JsException as error:
                return json.dumps({'error': str(error)})

            return json.dumps({'result': result})
"""


async def publish_release(
    ledger: Ledger, release: Release, *, seal: bool = True
) -> None:
    evaluation = release.evaluation
    await ledger.stage_policy(evaluation.policy_id, release.policy)
    await ledger.stage_evaluation(evaluation)

    for system in release.systems:
        await ledger.stage_system(evaluation.id, system)

    for case in release.cases:
        await ledger.stage_case(evaluation.id, case)

    for output in release.outputs:
        await ledger.stage_output(evaluation.id, output)

    for numeric in release.numerics:
        await ledger.stage_numerical(evaluation.id, numeric)

    if seal:
        await ledger.seal_evaluation(evaluation.id, 0)
        await publish_presentation(ledger, release)


async def publish_presentation(ledger: Ledger, release: Release) -> None:
    await ledger.stage_publication(release.publication)

    for rendered in release.rendered:
        await ledger.stage_rendered(release.publication.id, rendered)

    await ledger.seal_publication(release.publication.id, 0)
    await ledger.stage_points(release.points, 0)


def schema_statements() -> list[Statement]:
    statements: list[Statement] = []
    pending = ''

    for line in (
        files('assistant_bench_arena')
        .joinpath('schema.sql')
        .read_text()
        .splitlines(keepends=True)
    ):
        pending += line

        if sqlite3.complete_statement(pending):
            statements.append(Statement(sql=pending))
            pending = ''

    assert not pending.strip()
    return statements


class Reply(BaseModel):
    result: dict[str, JsonValue] | list[dict[str, JsonValue]] | None = None
    error: str | None = None


class D1Transport:
    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process
        self.lock = asyncio.Lock()

    async def request(
        self, statements: list[Statement], *, batch: bool = False
    ) -> dict[str, JsonValue] | list[dict[str, JsonValue]]:
        assert self.process.stdin is not None and self.process.stdout is not None
        payload = {
            'mode': 'batch' if batch else 'query',
            'statements': [item.model_dump(mode='json') for item in statements],
        }

        async with self.lock:
            self.process.stdin.write((json.dumps(payload) + '\n').encode())
            await self.process.stdin.drain()
            line = await asyncio.wait_for(self.process.stdout.readline(), 30)
            assert line, 'The local D1 client closed its protocol stream.'
            reply = Reply.model_validate_json(line)

        if reply.error is not None:
            match = _GUARD.fullmatch(reply.error)

            if match is not None:
                try:
                    reason = _REJECTION.validate_python(match[1])
                except ValidationError:
                    pass
                else:
                    raise CommandRejected(reason)

            raise RuntimeError(reply.error)

        assert reply.result is not None
        return reply.result

    async def execute(self, statement: Statement) -> None:
        await self.request([statement])

    async def query(self, statement: Statement) -> list[Row]:
        result = await self.request([statement])
        assert isinstance(result, dict)
        return TypeAdapter(list[Row]).validate_python(result['results'])

    async def rows(self, sql: str, *params: SqlValue) -> list[Row]:
        return await self.query(Statement(sql=sql, params=params))

    async def reset(self) -> None:
        objects = await self.rows(
            "SELECT type, name FROM sqlite_schema WHERE type IN ('view', 'table') "
            "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '_cf_%' "
            'ORDER BY type DESC, rowid DESC'
        )
        statements: list[Statement] = [Statement(sql='PRAGMA defer_foreign_keys = ON')]
        statements.extend(
            Statement(sql=f'DROP {item["type"]} "{item["name"]}"') for item in objects
        )
        await self.request(statements, batch=True)

    async def bootstrap(self, config: BootstrapConfiguration) -> None:
        await self.request(
            [*schema_statements(), *bootstrap_statements(config)], batch=True
        )


class D1Runtime:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.environment = dict(os.environ)

        for name in (
            'CLOUDFLARE_API_TOKEN',
            'CLOUDFLARE_API_KEY',
            'CLOUDFLARE_D1_TOKEN',
            'CLOUDFLARE_EMAIL',
        ):
            self.environment.pop(name, None)

        self.environment.update(
            PATH=os.pathsep.join(
                [str(Path(sys.executable).parent), self.environment['PATH']]
            ),
            WRANGLER_SEND_METRICS='false',
            XDG_CONFIG_HOME=str(directory / 'config'),
        )

    @contextmanager
    def owner(self) -> Generator[Self]:
        name = f'ledger-test-{uuid4().hex}'
        source = self.directory / 'src'
        source.mkdir()
        configuration = {
            'name': name,
            'main': str(source / 'owner.py'),
            'compatibility_date': '2026-10-24',
            'compatibility_flags': ['python_workers'],
            'd1_databases': [
                {
                    'binding': 'DB',
                    'database_name': name,
                    'database_id': str(uuid4()),
                }
            ],
        }
        client = {
            'name': name + '-client',
            'compatibility_date': '2026-10-24',
            'services': [
                {
                    'binding': 'MANAGEMENT',
                    'service': name,
                    'entrypoint': 'LocalManagement',
                    'remote': False,
                }
            ],
        }
        owner_config = self.directory / 'wrangler.jsonc'
        owner_config.write_text(json.dumps(configuration))
        (self.directory / 'pyproject.toml').write_text(
            dedent(f"""
                [project]
                name = "ledger-d1-test"
                version = "0.0.0"
                requires-python = ">=3.14"
                dependencies = [
                    "workers-runtime-sdk=={importlib.metadata.version('workers-runtime-sdk')}"
                ]

                [[tool.uv.index]]
                name = "pypi"
                url = "https://pypi.org/simple"
                default = true
            """)
        )
        (self.directory / 'node_modules').symlink_to(ROOT / 'node_modules')
        (self.directory / 'client.json').write_text(json.dumps(client))
        (source / 'owner.py').write_text(dedent(_OWNER))
        (self.directory / 'bridge.mjs').write_text(
            dedent(f"""
                import {{Console}} from 'node:console';
                import {{createRequire}} from 'node:module';
                import {{createInterface}} from 'node:readline';

                globalThis.console = new Console({{
                    stdout: process.stderr, stderr: process.stderr,
                }});
                const {{getPlatformProxy}} = createRequire(
                    {json.dumps(str(ROOT / 'package.json'))}
                )('wrangler');
                const platform = await getPlatformProxy({{
                    configPath: {json.dumps(str(self.directory / 'client.json'))},
                    envFiles: [], remoteBindings: false, persist: false,
                }});

                try {{
                    const input = createInterface({{input: process.stdin}});

                    for await (const line of input) {{
                        const result = await platform.env.MANAGEMENT.execute(line);
                        process.stdout.write(result + '\\n');
                    }}
                }} finally {{
                    await platform.dispose();
                }}
            """)
        )

        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]

        process = subprocess.Popen(
            [
                str(Path(sys.executable).with_name('pywrangler')),
                'dev',
                '--config',
                str(owner_config),
                '--local',
                '--ip',
                '127.0.0.1',
                '--port',
                str(port),
                '--persist-to',
                str(self.directory / 'state'),
            ],
            cwd=self.directory,
            env=self.environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        lines: queue.Queue[str | None] = queue.Queue()

        def record() -> None:
            assert process.stdout is not None

            with (self.directory / 'owner.log').open('w') as log:
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    lines.put(line)

            lines.put(None)

        reader = threading.Thread(target=record, daemon=True)
        reader.start()

        try:
            while True:
                line = lines.get(timeout=60)

                if line is None:
                    raise RuntimeError((self.directory / 'owner.log').read_text())

                if 'Ready on' in line:
                    break

            response = httpx.get(f'http://127.0.0.1:{port}', timeout=30)
            assert response.status_code == 200 and response.text == 'ready'

            yield self
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=30)
            reader.join(timeout=5)

    @asynccontextmanager
    async def client(self) -> AsyncGenerator[tuple[Ledger, D1Transport]]:
        with (self.directory / f'client-{uuid4().hex}.log').open('wb') as errors:
            process = await asyncio.create_subprocess_exec(
                'node',
                str(self.directory / 'bridge.mjs'),
                cwd=ROOT,
                env=self.environment,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=errors,
                limit=4_194_304,
            )
            transport = D1Transport(process)

            try:
                yield Ledger(transport), transport
            finally:
                assert process.stdin is not None
                process.stdin.close()
                await process.stdin.wait_closed()
                output, _ = await asyncio.wait_for(process.communicate(), 30)
                errors.flush()
                assert process.returncode == 0 and not output, (
                    output,
                    Path(errors.name).read_text(),
                )
