"""Command-line entry point."""

import argparse
import asyncio
import logging
from contextlib import suppress
from pathlib import Path

from .config import ServerConfig
from .server import Server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='filehost')
    commands = parser.add_subparsers(dest='command', required=True)
    serve = commands.add_parser('serve', help='serve the files under ROOT')
    serve.add_argument('root', type=Path)
    serve.add_argument('--host', default='127.0.0.1')
    serve.add_argument('--port', type=int, default=8000)
    serve.add_argument('--quiet', action='store_true', help='skip the access log')
    args = parser.parse_args(argv)

    if not args.root.is_dir():
        parser.error(f'{args.root} is not a directory')

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format='%(asctime)s %(message)s',
    )
    config = ServerConfig(root=args.root.resolve(), host=args.host, port=args.port)
    with suppress(KeyboardInterrupt):
        asyncio.run(_serve(config))
    return 0


async def _serve(config: ServerConfig) -> None:
    server = Server(config)
    port = await server.start()
    print(f'listening on http://{config.host}:{port}', flush=True)
    await server.serve_forever()
