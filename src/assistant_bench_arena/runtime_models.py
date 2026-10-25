"""Signed browser claims and trusted deployment settings."""

from datetime import datetime
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    SecretBytes,
    StrictBool,
    TypeAdapter,
    model_validator,
)

from assistant_bench_arena.models import Record


type Identifier = Annotated[str, Field(min_length=1, max_length=256)]
type Namespace = Annotated[str, Field(pattern=r'^[a-zA-Z0-9_.-]{1,128}$')]

_ORIGIN = TypeAdapter(AnyHttpUrl)


class Claims(Record):
    version: Literal[1] = 1
    namespace: Namespace
    issued_at: NonNegativeInt
    """Original issuance time in UTC milliseconds."""

    expires_at: NonNegativeInt
    """Absolute UTC milliseconds, retained across cookie replay."""

    @model_validator(mode='after')
    def lifetime(self) -> Self:
        if self.expires_at <= self.issued_at:
            raise ValueError('The expiration must follow issuance.')

        return self


class AdmissionClaims(Claims):
    flow: UUID
    operation: Literal['new', 'renew']
    original_credential: Identifier | None = None

    @model_validator(mode='after')
    def renewal(self) -> Self:
        if (self.operation == 'renew') != (self.original_credential is not None):
            raise ValueError('Renewal requires the original credential.')

        return self


class CredentialClaims(Claims):
    credential: Identifier
    rate_subject: Identifier
    """Opaque source-lineage request-limit subject, independent of actor attribution."""


class PairClaims(Claims):
    lease: Identifier


class RenderClaims(PairClaims):
    side: Literal['a', 'b']


class KeyVersion(Record):
    version: Namespace
    starts_at: NonNegativeInt
    """UTC epoch used to select the key for a fixed quota window."""

    key: SecretBytes

    @model_validator(mode='after')
    def strength(self) -> Self:
        if len(self.key.get_secret_value()) < 32:
            raise ValueError('A key must contain at least 32 bytes.')

        return self


class KeyRing(Record):
    versions: Annotated[tuple[KeyVersion, ...], Field(min_length=1)]
    """Oldest first. The newest signing key issues new capabilities."""

    @model_validator(mode='after')
    def ordered(self) -> Self:
        epochs = [key.starts_at for key in self.versions]
        names = [key.version for key in self.versions]

        if epochs != sorted(set(epochs)) or len(names) != len(set(names)):
            raise ValueError('Key versions require unique names and increasing epochs.')

        return self

    def at(self, timestamp: int) -> KeyVersion:
        for version in reversed(self.versions):
            if version.starts_at <= timestamp:
                return version

        raise ValueError('No retained key covers this window.')


class Profile(Record):
    namespace: Namespace
    app_origin: str
    render_origin: str

    @model_validator(mode='after')
    def origins(self) -> Self:
        origins = [urlsplit(self.app_origin), urlsplit(self.render_origin)]

        for original, value in zip(
            (self.app_origin, self.render_origin), origins, strict=True
        ):
            if (
                value.scheme not in {'http', 'https'}
                or not value.hostname
                or value.port == 0
                or value.username is not None
                or value.password is not None
                or value.path
                or value.query
                or value.fragment
            ):
                raise ValueError('An origin must contain only scheme and authority.')

            if str(_ORIGIN.validate_python(original)).removesuffix('/') != original:
                raise ValueError(
                    'An origin must use its canonical scheme, host and port.'
                )

            if value.scheme != 'https':
                raise ValueError('Voting origins require HTTPS.')

        if origins[0].hostname == origins[1].hostname:
            raise ValueError('The app and renderer require distinct cookie hosts.')

        return self


class ChallengeResult(BaseModel):
    """The provider's public success fields, validated before issuing a credential."""

    model_config = ConfigDict(extra='ignore')

    success: StrictBool
    challenge_ts: datetime
    hostname: str
    action: str
    cdata: str
