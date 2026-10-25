"""Same-origin browser operations with safe errors and receipt-first recovery."""

from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID, uuid4

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import HTMLResponse, JSONResponse

from assistant_bench_arena.admission import (
    ChallengeRejected,
    InvalidToken,
    NetworkIdentity,
)
from assistant_bench_arena.api_models import (
    AdmissionReplay,
    AdmissionRequest,
    AdmissionView,
    ErrorView,
    FreshnessRequest,
    FreshnessView,
    ReceiptQuery,
    ResumeSession,
    SessionView,
    StartSession,
    StatusView,
    VoteAcknowledgment,
    VoteRequest,
    WithdrawRequest,
)
from assistant_bench_arena.ledger_models import (
    Accept,
    Context,
    Credential,
    Receipt,
    VoteMeaning,
)
from assistant_bench_arena.models import FamilyView
from assistant_bench_arena.transport import CommandRejected
from assistant_bench_arena.voting import Voting
from assistant_bench_arena.worker.authentication import (
    CREDENTIAL_MS,
    ApiError,
    Authentication,
)
from assistant_bench_arena.worker.bindings import RateLimited
from assistant_bench_arena.worker.diagnostics import failure


@dataclass(frozen=True)
class Ingress:
    now: int
    network: NetworkIdentity
    cookies: str


INGRESS: ContextVar[Ingress] = ContextVar('arena_ingress')


class BrowserApi:
    def __init__(
        self, authentication: Authentication, voting: Voting, admission_shell: str
    ) -> None:
        self.authentication = authentication
        self.voting = voting

        if admission_shell.count('"__BENCH_ADMISSION_DATA__"') != 1:
            raise ValueError('The prepared admission document is invalid.')

        self.admission_shell = admission_shell

    async def middleware(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        try:
            return await call_next(request)
        except ApiError as error:
            return JSONResponse(
                ErrorView(error=error.code).model_dump(), status_code=error.status
            )
        except RequestValidationError:
            return JSONResponse({'error': 'invalid_request'}, status_code=400)
        except RateLimited:
            return JSONResponse({'error': 'rate_limited'}, status_code=429)
        except ChallengeRejected:
            return JSONResponse({'error': 'challenge_rejected'}, status_code=400)
        except CommandRejected as error:
            if error.reason in {'quota_exhausted', 'pending_limit'}:
                code, status = 'rate_limited', 429
            elif error.reason in {'session_stale', 'lease_stale', 'family_missing'}:
                code, status = 'not_found', 404
            else:
                code, status = 'conflict', 409

            return JSONResponse({'error': code}, status_code=status)
        except Exception as error:
            failure('http', error)
            return JSONResponse({'error': 'unavailable'}, status_code=503)

    async def status(self) -> StatusView:
        ingress = INGRESS.get()
        return await self.authentication.status(ingress.cookies, ingress.now)

    async def families(self) -> list[FamilyView]:
        return list(self.voting.catalog.values())

    async def admission_document(self, request: Request) -> Response:
        ingress = INGRESS.get()
        family = request.query_params.get('family')

        if family is not None and family not in self.voting.catalog:
            raise ApiError(404, 'not_found')

        flow = await self.authentication.flow(ingress.cookies, ingress.now)

        view = AdmissionView(
            command_id=flow.flow,
            action='bench_admit' if flow.operation == 'new' else 'bench_renew',
            sitekey=self.authentication.settings.sitekey,
            family=family,
        )

        payload = view.model_dump_json().replace('<', '\\u003c')
        body = self.admission_shell.replace('"__BENCH_ADMISSION_DATA__"', payload)
        response = HTMLResponse(body)
        response.headers['Content-Security-Policy'] = (
            "frame-src https://challenges.cloudflare.com; connect-src 'self'; "
            "style-src 'self'; base-uri 'none'; form-action 'none'; "
            "frame-ancestors 'none'; object-src 'none'"
        )
        response.headers['Set-Cookie'] = self.authentication.set_cookie(
            self.authentication.flow_cookie,
            self.authentication.flows.encode(flow),
            flow.expires_at,
            ingress.now,
        )
        return response

    async def admission(self, command: AdmissionRequest) -> Response:
        ingress = INGRESS.get()
        result = await self.authentication.admit(
            ingress.cookies,
            command.command_id,
            command.proof,
            ingress.network,
            ingress.now,
        )
        return self._admitted(
            result_cookie=self.authentication.issued_cookie(result, ingress.now)
        )

    async def admission_replay(self, command: AdmissionReplay) -> Response:
        ingress = INGRESS.get()
        result = await self.authentication.admit(
            ingress.cookies,
            command.command_id,
            command.proof,
            ingress.network,
            ingress.now,
            replay=True,
        )
        return self._admitted(
            result_cookie=self.authentication.issued_cookie(result, ingress.now)
        )

    def _admitted(self, *, result_cookie: str) -> Response:
        response = JSONResponse(StatusView(status='ready').model_dump())
        response.headers['Set-Cookie'] = result_cookie
        return response

    async def start(self, command: StartSession) -> SessionView:
        ingress, credential = await self._owner()
        return await self.voting.start(
            self._context(ingress, credential, command.command_id),
            command.family,
            self.authentication.network(ingress.network, 'issue', ingress.now),
        )

    async def resume(self, command: ResumeSession) -> SessionView:
        ingress, credential = await self._owner()
        return await self.voting.resume(
            self._context(ingress, credential, command.command_id),
            command.session_id,
            self.authentication.network(ingress.network, 'issue', ingress.now),
        )

    async def vote(self, command: VoteRequest) -> VoteAcknowledgment:
        ingress = INGRESS.get()

        try:
            pair = self.voting.pairs.decode(
                command.capability, ingress.now, recovery_ms=CREDENTIAL_MS
            )
        except InvalidToken:
            raise ApiError(409, 'conflict') from None

        if pair.lease != command.lease_id:
            raise ApiError(400, 'invalid_request')

        credential = await self.authentication.owner(
            ingress.cookies, ingress.now, grant=False
        )
        context = self._context(ingress, credential, command.command_id)
        meaning = self._meaning(command)
        receipt = await self.voting.ledger.resolve_receipt(context, meaning)

        if receipt is not None:
            return self._acknowledgment(receipt)

        if credential.challenge_until <= ingress.now:
            raise ApiError(403, 'renewal_required')

        if pair.expires_at <= ingress.now:
            raise ApiError(409, 'conflict')

        receipt = await self.voting.ledger.accept(
            context,
            Accept(
                lease_id=meaning.lease_id,
                choice=meaning.choice,
                defects=meaning.defects,
                acknowledgment=str(uuid4()),
                network=self.authentication.network(
                    ingress.network, 'accept', ingress.now
                ),
                attribution=self.authentication.attribution(
                    ingress.network, ingress.now
                ),
            ),
        )
        return self._acknowledgment(receipt)

    async def resolve(self, command: ReceiptQuery) -> VoteAcknowledgment:
        ingress, credential = await self._owner(grant=False)
        receipt = await self.voting.ledger.resolve_receipt(
            self._context(ingress, credential, command.command_id),
            self._meaning(command),
        )

        if receipt is None:
            raise ApiError(404, 'not_found')

        return self._acknowledgment(receipt)

    async def withdraw(self, command: WithdrawRequest) -> Response:
        ingress, credential = await self._owner()
        await self.voting.ledger.withdraw_ack(
            self._context(ingress, credential, command.command_id),
            command.session_id,
            command.acknowledgment,
        )
        return Response(status_code=204)

    async def freshness(self, command: FreshnessRequest) -> FreshnessView:
        return FreshnessView(
            status=await self.voting.ledger.freshness(command.export_id)
        )

    async def _owner(self, *, grant: bool = True) -> tuple[Ingress, Credential]:
        ingress = INGRESS.get()
        return ingress, await self.authentication.owner(
            ingress.cookies, ingress.now, grant=grant
        )

    @staticmethod
    def _context(ingress: Ingress, credential: Credential, command: UUID) -> Context:
        return Context(
            command_id=str(command), principal=credential.actor_id, now=ingress.now
        )

    @staticmethod
    def _meaning(command: ReceiptQuery) -> VoteMeaning:
        return VoteMeaning(
            lease_id=command.lease_id, choice=command.choice, defects=command.defects
        )

    @staticmethod
    def _acknowledgment(receipt: Receipt) -> VoteAcknowledgment:
        return VoteAcknowledgment(
            acknowledgment=receipt.acknowledgment, session_id=receipt.session_id
        )


async def invalid_request(request: Request, error: Exception) -> Response:
    return JSONResponse({'error': 'invalid_request'}, status_code=400)


async def missing_route(request: Request, error: Exception) -> Response:
    return JSONResponse({'error': 'not_found'}, status_code=404)


def application(api: BrowserApi) -> FastAPI:
    app = FastAPI(
        docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False
    )
    app.middleware('http')(api.middleware)
    app.add_exception_handler(RequestValidationError, invalid_request)
    app.add_exception_handler(HTTPException, missing_route)
    app.add_api_route('/api/status', api.status, methods=['GET'])
    app.add_api_route('/api/families', api.families, methods=['GET'])
    app.add_api_route('/admit', api.admission_document, methods=['GET'])
    app.add_api_route('/api/admission', api.admission, methods=['POST'])
    app.add_api_route('/api/admission/replay', api.admission_replay, methods=['POST'])
    app.add_api_route('/api/sessions', api.start, methods=['POST'], status_code=201)
    app.add_api_route('/api/sessions/resume', api.resume, methods=['POST'])
    app.add_api_route('/api/votes', api.vote, methods=['POST'])
    app.add_api_route('/api/votes/resolve', api.resolve, methods=['POST'])
    app.add_api_route('/api/sessions/withdraw', api.withdraw, methods=['POST'])
    app.add_api_route('/api/freshness', api.freshness, methods=['POST'])

    return app
