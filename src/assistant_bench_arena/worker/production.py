"""HTTP and private Service entrypoints for the production database owner."""

import html
import time

from js import Response as NativeResponse
from workers import Request, Response, WorkerEntrypoint

from assistant_bench_arena.admission import InvalidToken
from assistant_bench_arena.api_models import ErrorView
from assistant_bench_arena.management_domain import execute
from assistant_bench_arena.management_models import (
    RpcError,
    RpcFailure,
    RpcSuccess,
    parse_request,
)
from assistant_bench_arena.transport import CommandRejected
from assistant_bench_arena.worker.api import (
    RequestRejected,
    header,
    serve_http,
    validate_request,
)
from assistant_bench_arena.worker.authentication import ApiError
from assistant_bench_arena.worker.bindings import RateLimited
from assistant_bench_arena.worker.diagnostics import (
    FailureStage,
    failure,
    rejected,
)
from assistant_bench_arena.worker.http import INGRESS, Ingress
from assistant_bench_arena.worker.owner import AppBindings, owner


VOTE_PATHS = frozenset({'/vote', '/vote/'})
_DIRECTIVES = (
    "connect-src 'self'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'; object-src 'none'"
)


def _error_response(status: int, body: str) -> NativeResponse:
    return Response(
        body, status=status, headers={'Content-Type': 'application/json'}
    ).js_object


class OwnerEntrypoint(WorkerEntrypoint):
    def __init__(self, ctx: object, env: AppBindings) -> None:
        super().__init__(ctx, env)
        self.owner = owner(env)


class Default(OwnerEntrypoint):
    async def scheduled(self, controller: object, env: object, ctx: object) -> None:
        try:
            await self.owner.auth.ensure_ready()
            now = time.time_ns() // 1_000_000
            await self.owner.ledger.maintenance.expire(now)
            lag = await self.owner.ledger.maintenance.attribution_lag(now)
        except Exception:
            raise RuntimeError('Scheduled voting maintenance failed.') from None

        if lag > 0:
            raise RuntimeError('Scheduled voting cleanup has expired associations.')

    async def fetch(self, request: Request) -> NativeResponse:
        token = None
        stage: FailureStage = 'validation'
        private = True

        try:
            native = request.js_object
            method = native.method

            if method not in {'GET', 'POST'} or header(native, 'CF-Worker') is not None:
                raise RequestRejected

            path = validate_request(native, self.owner.settings.profile, method)

            if path.startswith('/api/'):
                now = time.time_ns() // 1_000_000
                stage = 'preflight'
                network = await self.owner.auth.preflight(
                    header(native, 'CF-Connecting-IP') or '', now
                )
                token = INGRESS.set(
                    Ingress(now, network, header(native, 'Cookie') or '')
                )
                stage = 'http'
                response = await serve_http(
                    await self.owner.browser(),
                    request,
                    self.owner.settings.profile,
                    self.env,
                )
            else:
                if method != 'GET' or path not in VOTE_PATHS:
                    raise RequestRejected

                stage = 'assets'
                asset = await self.owner.bindings.ASSETS.fetch(native)
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
                    f'frame-src {self.owner.settings.profile.render_origin} '
                    f'https://challenges.cloudflare.com; {_DIRECTIVES}',
                )
                private = False
        except ApiError as error:
            response = _error_response(
                error.status, ErrorView(error=error.code).model_dump_json()
            )
        except RateLimited:
            response = _error_response(429, '{"error":"rate_limited"}')
        except RequestRejected:
            response = _error_response(400, '{"error":"invalid_request"}')
        except Exception as error:
            failure(stage, error)
            response = _error_response(503, '{"error":"unavailable"}')
        finally:
            if token is not None:
                INGRESS.reset(token)

        if private:
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
        except Exception as error:
            failure('render_lookup', error)
            return RpcFailure(error=RpcError(reason='unavailable')).model_dump_json(
                exclude_none=True
            )


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
            return RpcFailure(error=RpcError(reason=error.reason)).model_dump_json(
                exclude_none=True
            )
        except ValueError as error:
            rejected(error)
            return RpcFailure(
                error=RpcError(reason='invalid_request', detail=str(error))
            ).model_dump_json(exclude_none=True)
        except Exception as error:
            failure('management', error)
            return RpcFailure(error=RpcError(reason='unavailable')).model_dump_json(
                exclude_none=True
            )
