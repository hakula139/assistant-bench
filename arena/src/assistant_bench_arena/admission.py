"""Purpose-bound native signatures and privacy-separated network identities."""

import hashlib
import hmac
from datetime import UTC, datetime
from email.utils import format_datetime
from http.cookies import SimpleCookie
from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network

from itsdangerous import BadData, URLSafeTimedSerializer
from pydantic import ValidationError

from assistant_bench_arena.runtime_models import (
    AdmissionClaims,
    ChallengeResult,
    Claims,
    KeyRing,
    KeyVersion,
)


class InvalidToken(Exception):
    """A signed capability is invalid for this purpose, profile, or time."""


class ChallengeRejected(Exception):
    """The provider did not verify this exact admission flow."""


class TokenCodec[T: Claims]:
    def __init__(
        self,
        model: type[T],
        keys: KeyRing,
        namespace: str,
        purpose: str,
        lifetime_ms: int,
    ) -> None:
        self.model: type[T] = model
        self.namespace: str = namespace
        self.lifetime_ms: int = lifetime_ms
        self.serializer: URLSafeTimedSerializer = URLSafeTimedSerializer(
            [version.key.get_secret_value() for version in keys.versions],
            salt=f'assistant-bench:{namespace}:{purpose}:1',
            signer_kwargs={'digest_method': hashlib.sha256},
        )

    def encode(self, claims: T) -> str:
        self._validate(claims, claims.issued_at)
        return self.serializer.dumps(claims.model_dump_json())

    def decode(self, token: str, now: int, *, recovery_ms: int = 0) -> T:
        """Decode recovery claims for the caller's exact committed-result check."""
        if not token or len(token) > 2048:
            raise InvalidToken

        try:
            payload: object = self.serializer.loads(
                token, max_age=(self.lifetime_ms + recovery_ms) // 1000
            )

            if not isinstance(payload, str):
                raise InvalidToken

            claims = self.model.model_validate_json(payload)
        except BadData, ValidationError:
            raise InvalidToken from None

        self._validate(claims, now, recovery_ms=recovery_ms)
        return claims

    def _validate(self, claims: T, now: int, *, recovery_ms: int = 0) -> None:
        if (
            claims.namespace != self.namespace
            or claims.issued_at > now
            or claims.expires_at + recovery_ms <= now
            or claims.expires_at - claims.issued_at > self.lifetime_ms
        ):
            raise InvalidToken


class NetworkIdentity:
    def __init__(self, address: str) -> None:
        parsed = ip_address(address)

        if isinstance(parsed, IPv6Address) and parsed.scope_id is not None:
            raise ValueError('The ingress address cannot have a zone identifier.')

        if isinstance(parsed, IPv6Address) and parsed.ipv4_mapped is not None:
            parsed = parsed.ipv4_mapped

        if parsed == IPv6Address('2a06:98c0:3600::103'):
            raise ValueError('The ingress does not identify a direct visitor.')

        self.address: str = str(parsed)
        self.prefix: str = str(
            ip_network(
                (parsed, 24 if isinstance(parsed, IPv4Address) else 56), strict=False
            )
        )

    def quota(
        self,
        keys: KeyRing,
        namespace: str,
        operation: str,
        scope: str,
        window_start: int,
        window_ms: int,
    ) -> str:
        key = keys.at(window_start)
        return _tag(
            key,
            namespace,
            'quota',
            operation,
            scope,
            str(window_start),
            str(window_ms),
            self._subject(scope),
        )

    def attribution(self, key: KeyVersion, namespace: str, scope: str) -> str:
        return _tag(key, namespace, 'attribution', scope, self._subject(scope))

    def _subject(self, scope: str) -> str:
        match scope:
            case 'exact':
                return self.address
            case 'prefix':
                return self.prefix
            case _:
                raise ValueError('A network scope must be exact or prefix.')


def _tag(key: KeyVersion, namespace: str, *parts: str) -> str:
    payload = '\0'.join(('assistant-bench', namespace, key.version, *parts))
    return hmac.new(
        key.key.get_secret_value(), payload.encode('utf-8'), hashlib.sha256
    ).hexdigest()


def verify_challenge(
    body: bytes, context: AdmissionClaims, hostname: str, now: int
) -> None:
    if len(body) > 8192:
        raise ChallengeRejected

    try:
        result = ChallengeResult.model_validate_json(body)
    except ValidationError:
        raise ChallengeRejected from None

    timestamp = result.challenge_ts

    if timestamp.tzinfo is None:
        raise ChallengeRejected

    age_ms = now - int(timestamp.timestamp() * 1000)

    if (
        not result.success
        or result.hostname != hostname
        or result.action
        != f'bench_{"admit" if context.operation == "new" else "renew"}'
        or result.cdata != str(context.flow)
        or not 0 <= age_ms <= 300_000
    ):
        raise ChallengeRejected


def cookie_header(
    name: str, value: str, expires_at: int, now: int, *, secure: bool
) -> str:
    cookie = SimpleCookie()
    cookie[name] = value
    morsel = cookie[name]
    morsel['path'] = '/'
    morsel['httponly'] = True
    morsel['samesite'] = 'Strict'
    morsel['max-age'] = max(0, (expires_at - now) // 1000)
    morsel['expires'] = format_datetime(
        datetime.fromtimestamp(expires_at / 1000, UTC), usegmt=True
    )

    if secure:
        morsel['secure'] = True

    return morsel.OutputString()
