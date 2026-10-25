"""Isolated HTML delivery through the sole render capability Service."""

import time
from typing import Protocol
from urllib.parse import urlsplit

from js import Response as NativeResponse
from pydantic import TypeAdapter
from workers import Request, Response, WorkerEntrypoint

from assistant_bench_arena.admission import InvalidToken, NetworkIdentity, TokenCodec
from assistant_bench_arena.management_models import RpcFailure, RpcResult
from assistant_bench_arena.read_models import RenderedContent
from assistant_bench_arena.runtime_models import KeyRing, RenderClaims
from assistant_bench_arena.voting import RENDER_MS
from assistant_bench_arena.worker.api import header
from assistant_bench_arena.worker.bindings import (
    NativeLimiter,
    RateLimited,
    enforce_limit,
)
from assistant_bench_arena.worker.settings import ProfileBindings, profile


class RenderLookup(Protocol):
    async def lookup(self, capability: str) -> str: ...


class RenderBindings(ProfileBindings, Protocol):
    RENDER_KEYS: str
    RENDER_LIMIT: NativeLimiter
    LOOKUP: RenderLookup


class Default(WorkerEntrypoint):
    def __init__(self, ctx: object, env: RenderBindings) -> None:
        super().__init__(ctx, env)
        self.bindings = env

        try:
            self.profile = profile(env)
            self.keys = KeyRing.model_validate_json(env.RENDER_KEYS)
        except ValueError:
            raise ValueError('The renderer configuration is invalid.') from None

        self.capabilities = TokenCodec(
            RenderClaims, self.keys, self.profile.namespace, 'render', RENDER_MS
        )

    async def fetch(self, request: Request) -> NativeResponse:
        status, body = 404, ''

        try:
            now = time.time_ns() // 1_000_000
            native = request.js_object
            url = urlsplit(native.url)

            if (
                native.method != 'GET'
                or f'{url.scheme}://{url.netloc}' != self.profile.render_origin
                or not url.path.startswith('/render/')
                or len(url.path) > 2056
                or url.query
                or url.fragment
                or '%' in url.path
                or '\\' in url.path
                or header(native, 'Sec-Fetch-Dest') not in {None, 'iframe'}
                or header(native, 'CF-Worker') is not None
            ):
                raise InvalidToken

            address = header(native, 'CF-Connecting-IP') or ''
            network = NetworkIdentity(address)
            await enforce_limit(
                self.bindings.RENDER_LIMIT,
                network.quota(
                    self.keys,
                    self.profile.namespace,
                    'render',
                    'exact',
                    now // 60_000 * 60_000,
                    60_000,
                ),
            )
            capability = url.path.removeprefix('/render/')
            self.capabilities.decode(capability, now)
            serialized = await self.bindings.LOOKUP.lookup(capability)

            if len(serialized.encode()) > 1_048_576:
                raise ValueError('The rendered document exceeds its limit.')

            result = TypeAdapter(RpcResult[RenderedContent | None]).validate_json(
                serialized
            )

            if not isinstance(result, RpcFailure) and result.result is not None:
                if result.result.render != 'html':
                    raise ValueError('The renderer received an incompatible output.')

                status, body = 200, result.result.content
        except RateLimited:
            status = 429
        except InvalidToken, ValueError:
            pass
        except Exception:
            status = 503

        return Response(
            body,
            status=status,
            headers={
                'Content-Type': 'text/html; charset=utf-8',
                'Cache-Control': 'no-store',
                'Referrer-Policy': 'no-referrer',
                'X-Content-Type-Options': 'nosniff',
                'Content-Security-Policy': (
                    "default-src 'none'; script-src 'unsafe-inline'; "
                    "style-src 'unsafe-inline'; img-src data:; sandbox allow-scripts; "
                    f'frame-ancestors {self.profile.app_origin}; '
                    "base-uri 'none'; form-action 'none'"
                ),
            },
        ).js_object
