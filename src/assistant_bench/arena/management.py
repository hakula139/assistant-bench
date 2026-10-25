"""Native Service-binding transport to an already-running arena owner."""

import asyncio
import os
import signal
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from pydantic import JsonValue, TypeAdapter

from assistant_bench.paths import REPO_ROOT
from assistant_bench_arena import management_models
from assistant_bench_arena.ledger_models import (
    ExportSection,
    Page,
    Row,
)
from assistant_bench_arena.management_models import RpcFailure, RpcResult
from assistant_bench_arena.serialization import canonical
from assistant_bench_arena.transport import CommandRejected, Rejection


_RPC = TypeAdapter[RpcResult[JsonValue]](RpcResult[JsonValue])
_ROW = TypeAdapter(Row)
_REJECTION = TypeAdapter[Rejection](Rejection.__value__)
_PAGE = TypeAdapter(Page)
_FRAME_BYTES = 4 * 1024 * 1024
"""Includes JSON escaping of the ledger's bounded canonical body strings."""


class Management:
    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process
        self.lock = asyncio.Lock()
        self.sequence = 0

    async def domain[Result](
        self, request: management_models.Request, adapter: TypeAdapter[Result]
    ) -> Result:
        management_models.parse_request(canonical(request))
        stdin, stdout = self.process.stdin, self.process.stdout

        if stdin is None or stdout is None:
            raise ValueError('The arena management protocol is not connected.')

        async with self.lock:
            identity = self.sequence
            self.sequence += 1
            payload: dict[str, JsonValue] = {
                'id': identity,
                'operation': 'command',
                'request': request.model_dump(mode='json'),
            }

            try:
                stdin.write(f'{canonical(payload)}\n'.encode())
                await stdin.drain()
                line = await asyncio.wait_for(stdout.readline(), 30)
            except (BrokenPipeError, ConnectionResetError, TimeoutError) as error:
                raise ValueError('The arena owner is unavailable.') from error

            if not line:
                raise ValueError('The arena owner closed its management connection.')

            reply = _ROW.validate_json(line)
            received = reply.pop('id', None)
            if type(received) is not int or received != identity:
                raise ValueError(
                    'The arena owner returned an unrelated management response.'
                )

            envelope = _RPC.validate_json(canonical(reply))

        if isinstance(envelope, RpcFailure):
            reason = envelope.error.reason
            if reason == 'unavailable':
                raise ValueError('The arena owner is unavailable.')

            raise CommandRejected(_REJECTION.validate_python(reason))

        return adapter.validate_json(canonical(envelope.result))

    async def page(
        self,
        export_id: str,
        section: ExportSection,
        cursor: str | None = None,
        *,
        limit: int = 250,
    ) -> Page:
        return await self.domain(
            management_models.ExportPage(
                export_id=export_id, section=section, cursor=cursor, limit=limit
            ),
            _PAGE,
        )


@asynccontextmanager
async def connect(config: Path) -> AsyncGenerator[Management]:
    """Consume the runtime's Service-only descriptor without creating a D1 owner."""
    environment = {
        name: os.environ[name]
        for name in (
            'HOME',
            'LANG',
            'LC_ALL',
            'NIX_SSL_CERT_FILE',
            'PATH',
            'SSL_CERT_FILE',
            'TMPDIR',
            'XDG_CACHE_HOME',
            'XDG_CONFIG_HOME',
        )
        if name in os.environ
    }

    for name in ('CLOUDFLARE_API_TOKEN', 'CLOUDFLARE_ACCOUNT_ID'):
        if name in os.environ:
            environment[name] = os.environ[name]

    environment['WRANGLER_SEND_METRICS'] = 'false'
    process = await asyncio.create_subprocess_exec(
        'node',
        str(REPO_ROOT / 'arena' / 'management.mjs'),
        str(config),
        cwd=REPO_ROOT,
        env=environment,
        start_new_session=True,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        limit=_FRAME_BYTES,
    )
    try:
        yield Management(process)
    finally:
        if process.stdin is not None:
            process.stdin.close()

        try:
            await asyncio.wait_for(process.wait(), 10)
        except TimeoutError:
            _signal(process, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), 10)
            except TimeoutError:
                _signal(process, signal.SIGKILL)
                await process.wait()


def _signal(process: asyncio.subprocess.Process, value: signal.Signals) -> None:
    try:
        os.killpg(process.pid, value)
    except ProcessLookupError:
        return
