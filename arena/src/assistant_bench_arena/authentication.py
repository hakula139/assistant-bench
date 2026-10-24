"""Cookie ownership, challenge admission and trusted network budgets."""

import hashlib
from http.cookies import CookieError, SimpleCookie
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import TypeAdapter

from assistant_bench_arena.admission import (
    InvalidToken,
    NetworkIdentity,
    TokenCodec,
    cookie_header,
    verify_challenge,
)
from assistant_bench_arena.api_models import ErrorCode, StatusView
from assistant_bench_arena.bindings import NativeLimiter, enforce_limit, siteverify
from assistant_bench_arena.ledger import Ledger
from assistant_bench_arena.ledger_models import (
    Admission,
    AdmissionResult,
    Attribution,
    Context,
    Credential,
    Network,
    NetworkTag,
)
from assistant_bench_arena.runtime_models import AdmissionClaims, CredentialClaims
from assistant_bench_arena.serialization import digest
from assistant_bench_arena.settings import Settings


CREDENTIAL_MS = 2_592_000_000
FLOW_MS = 600_000
GRANT_MS = 86_400_000


class ApiError(Exception):
    def __init__(self, status: int, code: ErrorCode) -> None:
        self.status = status
        self.code: ErrorCode = code
        super().__init__(code)


class Authentication:
    def __init__(
        self,
        settings: Settings,
        ledger: Ledger,
        ingress: NativeLimiter,
        prefixes: NativeLimiter,
        deployment: NativeLimiter,
        actors: NativeLimiter,
        challenge_secret: str,
        *,
        challenge_endpoint: str = 'https://challenges.cloudflare.com/turnstile/v0/siteverify',
    ) -> None:
        self.settings = settings
        self.ledger = ledger
        self.ingress = ingress
        self.prefixes = prefixes
        self.deployment = deployment
        self.actors = actors
        self.challenge_secret = challenge_secret
        self.challenge_endpoint = challenge_endpoint
        self.ready = False
        profile = settings.profile
        self.credentials = TokenCodec(
            CredentialClaims,
            settings.sessions,
            profile.namespace,
            'credential',
            CREDENTIAL_MS,
        )
        self.flows = TokenCodec(
            AdmissionClaims, settings.sessions, profile.namespace, 'admission', FLOW_MS
        )
        self.credential_cookie = '__Host-bench-credential'
        self.flow_cookie = '__Host-bench-admission'

    async def ensure_ready(self) -> None:
        if self.ready:
            return

        deployment = await self.ledger.status()

        if (
            deployment is None
            or deployment.model_dump(exclude={'schema_id'})
            != self.settings.bootstrap.model_dump()
        ):
            raise ValueError('The initialized profile does not match this owner.')

        self.ready = True

    async def preflight(self, address: str, now: int) -> NetworkIdentity:
        try:
            network = NetworkIdentity(address)
        except ValueError:
            raise ApiError(400, 'invalid_request') from None

        await enforce_limit(
            self.ingress,
            network.quota(
                self.settings.network,
                self.settings.profile.namespace,
                'request',
                'exact',
                now // 60_000 * 60_000,
                60_000,
            ),
        )
        await enforce_limit(
            self.prefixes,
            network.quota(
                self.settings.network,
                self.settings.profile.namespace,
                'request',
                'prefix',
                now // 60_000 * 60_000,
                60_000,
            ),
        )
        await enforce_limit(self.deployment, self.settings.profile.namespace)
        return network

    def claims(
        self, cookies: str, now: int, *, recovery: bool = False
    ) -> CredentialClaims:
        return self.credentials.decode(
            cookie(cookies, self.credential_cookie),
            now,
            recovery_ms=FLOW_MS if recovery else 0,
        )

    async def owner(self, cookies: str, now: int, *, grant: bool = True) -> Credential:
        try:
            claims = self.claims(cookies, now)
        except InvalidToken:
            raise ApiError(401, 'admission_required') from None

        await enforce_limit(self.actors, claims.rate_subject)
        await self.ensure_ready()
        credential = await self.ledger.credential(claims.credential, now)

        if credential is None or not self._matches(claims, credential):
            raise ApiError(401, 'admission_required')

        if grant and credential.challenge_until <= now:
            raise ApiError(403, 'renewal_required')

        return credential

    async def status(self, cookies: str, now: int) -> StatusView:
        try:
            credential = await self.owner(cookies, now, grant=False)
        except ApiError as error:
            if error.code != 'admission_required':
                raise

            status: Literal['admission_required', 'renewal_required', 'ready'] = (
                'admission_required'
            )
        else:
            status = 'ready' if credential.challenge_until > now else 'renewal_required'

        return StatusView(status=status)

    async def flow(self, cookies: str, now: int) -> AdmissionClaims:
        try:
            previous = self.flows.decode(cookie(cookies, self.flow_cookie), now)
        except InvalidToken:
            previous = None

        if previous is not None:
            if previous.operation == 'new':
                return previous

            try:
                original = self.claims(cookies, now, recovery=True)
            except InvalidToken:
                pass
            else:
                if original.credential == previous.original_credential:
                    return previous

        try:
            original_credential = await self.owner(cookies, now, grant=False)
        except ApiError as error:
            if error.code != 'admission_required':
                raise

            original_credential = None

        return AdmissionClaims(
            namespace=self.settings.profile.namespace,
            issued_at=now,
            expires_at=now + FLOW_MS,
            flow=uuid4(),
            operation='new' if original_credential is None else 'renew',
            original_credential=None
            if original_credential is None
            else original_credential.id,
        )

    async def admit(
        self,
        cookies: str,
        command: UUID,
        proof: str | None,
        network: NetworkIdentity,
        now: int,
        *,
        replay: bool = False,
    ) -> AdmissionResult:
        try:
            flow = self.flows.decode(cookie(cookies, self.flow_cookie), now)
        except InvalidToken:
            raise ApiError(401, 'admission_required') from None

        if flow.flow != command:
            raise ApiError(409, 'conflict')

        original: Credential | None = None

        if flow.operation == 'renew':
            try:
                claims = self.claims(cookies, now, recovery=True)
            except InvalidToken:
                raise ApiError(401, 'admission_required') from None

            if claims.credential != flow.original_credential:
                raise ApiError(409, 'conflict')

            await enforce_limit(self.actors, claims.rate_subject)
            await self.ensure_ready()
            original = await self.ledger.renewal_credential(
                claims.credential, str(command), now
            )

            if original is None or not self._matches(claims, original):
                raise ApiError(401, 'admission_required')

        await self.ensure_ready()
        context_digest = digest(flow)
        context = Context(
            command_id=str(command),
            principal=context_digest if original is None else original.actor_id,
            now=now,
        )
        proof_digest = (
            None if proof is None else hashlib.sha256(proof.encode()).hexdigest()
        )

        result = await self.ledger.resolve_admission(
            context,
            flow.operation,
            context_digest,
            flow.original_credential,
            proof_digest,
        )

        if result is not None:
            return result

        if replay:
            raise ApiError(404, 'not_found')

        if original is not None:
            current = await self.ledger.credential(original.id, now)

            if current is None:
                raise ApiError(401, 'admission_required')

        if proof is None:
            raise ApiError(400, 'invalid_request')

        response = await siteverify(
            self.challenge_secret,
            proof,
            network.address,
            flow.flow,
            endpoint=self.challenge_endpoint,
        )
        hostname = urlsplit(self.settings.profile.app_origin).hostname
        assert hostname is not None
        verify_challenge(response, flow, hostname, now)

        assert proof_digest is not None
        return await self.ledger.admit(
            context,
            Admission(
                operation=flow.operation,
                context_digest=context_digest,
                proof_digest=proof_digest,
                original_credential=flow.original_credential,
                actor_id=str(uuid4()) if original is None else original.actor_id,
                credential_id=str(uuid4()),
                rate_subject=str(uuid4())
                if original is None
                else original.rate_subject,
                expires_at=now + CREDENTIAL_MS,
                challenge_until=now + GRANT_MS,
                network=self.network(
                    network, 'admit' if flow.operation == 'new' else 'renew', now
                ),
            ),
        )

    def network(
        self,
        network: NetworkIdentity,
        operation: Literal['admit', 'renew', 'issue', 'accept'],
        now: int,
    ) -> Network:
        keys = self.settings.network
        namespace = self.settings.profile.namespace
        return Network(
            tags=tuple(
                NetworkTag(
                    scope=quota.scope,
                    window_ms=quota.window_ms,
                    tag=network.quota(
                        keys,
                        namespace,
                        operation,
                        quota.scope,
                        now // quota.window_ms * quota.window_ms,
                        quota.window_ms,
                    ),
                )
                for quota in self.settings.bootstrap.quotas
                if quota.operation == operation
                and (quota.scope == 'exact' or quota.scope == 'prefix')
            )
        )

    def attribution(
        self, network: NetworkIdentity, now: int
    ) -> tuple[Attribution, ...]:
        retention = self.settings.bootstrap.attribution_ms

        key = self.settings.network.at(now)
        return tuple(
            Attribution(
                version=key.version,
                kind=scope,
                tag=network.attribution(key, self.settings.profile.namespace, scope),
                expires_at=now + retention,
            )
            for scope in ('exact', 'prefix')
        )

    def issued_cookie(self, result: AdmissionResult, now: int) -> str:
        claims = CredentialClaims(
            namespace=self.settings.profile.namespace,
            issued_at=result.issued_at,
            expires_at=result.expires_at,
            credential=result.credential_id,
            rate_subject=result.rate_subject,
        )
        return self.set_cookie(
            self.credential_cookie,
            self.credentials.encode(claims),
            claims.expires_at,
            now,
        )

    def set_cookie(self, name: str, value: str, expires_at: int, now: int) -> str:
        return cookie_header(name, value, expires_at, now, secure=True)

    @staticmethod
    def _matches(claims: CredentialClaims, credential: Credential) -> bool:
        return (
            claims.rate_subject == credential.rate_subject
            and claims.issued_at == credential.issued_at
            and claims.expires_at == credential.expires_at
        )


def cookie(header: str, name: str) -> str:
    if (
        len(header) > 8192
        or sum(part.partition('=')[0].strip() == name for part in header.split(';'))
        != 1
    ):
        raise InvalidToken

    values = SimpleCookie()

    try:
        values.load(header)
    except CookieError:
        raise InvalidToken from None

    value = values.get(name)

    if value is None:
        raise InvalidToken

    return TypeAdapter(str).validate_python(value.value)
