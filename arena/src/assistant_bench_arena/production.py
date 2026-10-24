"""HTTP and private Service entrypoints for the production database owner."""

import html
import time

from js import Response as NativeResponse
from pyodide.ffi import JsNull
from workers import Request, Response, WorkerEntrypoint

from assistant_bench_arena.admission import InvalidToken
from assistant_bench_arena.api import RequestRejected, serve_http, validate_request
from assistant_bench_arena.management_domain import execute, parse_request
from assistant_bench_arena.management_models import RpcError, RpcFailure, RpcSuccess
from assistant_bench_arena.owner import AppBindings, owner
from assistant_bench_arena.transport import CommandRejected


class OwnerEntrypoint(WorkerEntrypoint):
    def __init__(self, ctx: object, env: AppBindings) -> None:
        super().__init__(ctx, env)
        self.owner = owner(env)


class Default(OwnerEntrypoint):
    async def scheduled(self, controller: object, env: object, ctx: object) -> None:
        try:
            await self.owner.auth.ensure_ready()
            await self.owner.ledger.maintenance.expire(time.time_ns() // 1_000_000)
        except Exception:
            raise RuntimeError('Scheduled voting maintenance failed.') from None

    async def fetch(self, request: Request) -> NativeResponse:
        try:
            native = request.js_object

            if native.method not in {'GET', 'POST'}:
                raise RequestRejected

            path = validate_request(native, self.owner.settings.profile, native.method)

            if path.startswith('/api/') or path == '/admit':
                return await serve_http(
                    await self.owner.browser(),
                    request,
                    self.owner.settings.profile,
                    self.env,
                )

            if native.method != 'GET' or path == '/api':
                raise RequestRejected

            asset = await self.owner.bindings.ASSETS.fetch(native)
            content_type = asset.headers.get('Content-Type')

            if isinstance(content_type, str) and content_type.startswith('text/html'):
                body = await asset.text()
                origin = html.escape(
                    self.owner.settings.profile.render_origin, quote=True
                )
                body = body.replace(
                    '</head>',
                    f'<meta name="bench-render-origin" content="{origin}"></head>',
                    1,
                )
                response = NativeResponse.new(body, asset.js_object)
                response.headers.set(
                    'Content-Security-Policy',
                    f'frame-src {self.owner.settings.profile.render_origin}; '
                    "connect-src 'self'; base-uri 'none'; form-action 'none'; "
                    "frame-ancestors 'none'; object-src 'none'",
                )
            else:
                stream = asset.js_object.body
                response = NativeResponse.new(
                    None if isinstance(stream, JsNull) else stream, asset.js_object
                )
        except RequestRejected:
            response = Response(
                '{"error":"invalid_request"}',
                status=400,
                headers={'Content-Type': 'application/json'},
            ).js_object
        except Exception:
            response = Response(
                '{"error":"unavailable"}',
                status=503,
                headers={'Content-Type': 'application/json'},
            ).js_object

        response.headers.set('Cache-Control', 'no-store')
        response.headers.set('Referrer-Policy', 'no-referrer')
        response.headers.set('X-Content-Type-Options', 'nosniff')
        return response


class RenderLookup(OwnerEntrypoint):
    async def lookup(self, capability: str) -> str:
        try:
            now = time.time_ns() // 1_000_000
            claims = self.owner.renders.decode(capability, now)
            await self.owner.auth.ensure_ready()
            rendered = await self.owner.ledger.read.render(
                claims.lease, claims.side, now
            )

            if rendered is not None and rendered.render != 'html':
                rendered = None

            result = RpcSuccess(result=rendered).model_dump_json()

            if len(result.encode()) > 1_048_576:
                raise ValueError('The rendered response exceeds its limit.')

            return result
        except InvalidToken, CommandRejected:
            return RpcSuccess[None](result=None).model_dump_json()
        except Exception:
            return RpcFailure(error=RpcError(reason='unavailable')).model_dump_json()


class Management(OwnerEntrypoint):
    async def command(self, serialized: str) -> str:
        try:
            request = parse_request(serialized)
            await self.owner.auth.ensure_ready()
            result = await execute(
                self.owner.ledger, request, time.time_ns() // 1_000_000
            )
            return RpcSuccess(result=result).model_dump_json()
        except CommandRejected as error:
            return RpcFailure(error=RpcError(reason=error.reason)).model_dump_json()
        except Exception:
            return RpcFailure(error=RpcError(reason='unavailable')).model_dump_json()
