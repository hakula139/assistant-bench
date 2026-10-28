"""An asyncio server that keeps connections alive between requests."""

import asyncio
import logging
from contextlib import suppress

from .config import ServerConfig
from .handlers import error, handle
from .http import BadRequest, Response, read_request, write_response


log = logging.getLogger('filehost')


class Server:
    def __init__(self, config: ServerConfig) -> None:
        self.config = config
        self._server: asyncio.Server | None = None

    async def start(self) -> int:
        """Start listening and return the bound port."""
        self._server = await asyncio.start_server(
            self._connection, self.config.host, self.config.port
        )
        return self._server.sockets[0].getsockname()[1]

    async def serve_forever(self) -> None:
        if self._server is None:
            raise RuntimeError('Start the server first.')
        await self._server.serve_forever()

    async def _connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info('peername')
        try:
            await self._exchange(reader, writer, peer)
        except ConnectionError:
            pass
        finally:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()

    async def _exchange(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        peer: object,
    ) -> None:
        while True:
            try:
                async with asyncio.timeout(self.config.idle_timeout):
                    request = await read_request(reader)
            except TimeoutError:
                return
            except BadRequest as rejected:
                log.info('%s rejected: %s', peer, rejected)
                await write_response(
                    writer, error(rejected.status), include_body=True, keep_alive=False
                )
                return

            if request is None:
                return

            try:
                response = handle(request, self.config)
            except OSError:
                log.exception('%s %s %s failed', peer, request.method, request.target)
                response = error(500)

            keep_alive = request.keep_alive
            await write_response(
                writer,
                response,
                include_body=request.method != 'HEAD',
                keep_alive=keep_alive,
            )
            _log_access(peer, request.method, request.target, response)
            if not keep_alive:
                return


def _log_access(peer: object, method: str, target: str, response: Response) -> None:
    log.info(
        '%s "%s %s" %d %d',
        peer,
        method,
        target,
        response.status,
        response.content_length,
    )
