"""The sole database owner shared by HTTP and confined native Services."""

from typing import TYPE_CHECKING, Protocol

from fastapi import FastAPI
from pydantic import TypeAdapter
from workers import Response

from assistant_bench_arena.admission import TokenCodec
from assistant_bench_arena.ledger import Ledger
from assistant_bench_arena.models import FamilyView
from assistant_bench_arena.runtime_models import PairClaims, RenderClaims
from assistant_bench_arena.voting import LEASE_MS, RENDER_MS, Voting
from assistant_bench_arena.worker.authentication import Authentication
from assistant_bench_arena.worker.bindings import (
    D1Transport,
    NativeDatabase,
    NativeLimiter,
)
from assistant_bench_arena.worker.http import BrowserApi, application
from assistant_bench_arena.worker.settings import ConfigurationBindings, Settings


if TYPE_CHECKING:
    from js import Request


class Assets(Protocol):
    async def fetch(self, request: Request) -> Response: ...


class AppBindings(ConfigurationBindings, Protocol):
    DB: NativeDatabase
    ASSETS: Assets
    INGRESS_LIMIT: NativeLimiter
    PREFIX_LIMIT: NativeLimiter
    DEPLOYMENT_LIMIT: NativeLimiter
    ACTOR_LIMIT: NativeLimiter
    TURNSTILE_SECRET: str


class Owner:
    def __init__(self, bindings: AppBindings) -> None:
        self.bindings = bindings

        try:
            self.settings = Settings(bindings)
        except ValueError:
            raise ValueError('The voting configuration is invalid.') from None

        self.transport = D1Transport(bindings.DB)
        self.ledger = Ledger(self.transport)
        self.http: FastAPI | None = None

        self.auth = Authentication(
            self.settings,
            self.ledger,
            bindings.INGRESS_LIMIT,
            bindings.PREFIX_LIMIT,
            bindings.DEPLOYMENT_LIMIT,
            bindings.ACTOR_LIMIT,
            bindings.TURNSTILE_SECRET,
        )

        self.pairs = TokenCodec(
            PairClaims,
            self.settings.sessions,
            self.settings.profile.namespace,
            'pair',
            LEASE_MS,
        )

        self.renders = TokenCodec(
            RenderClaims,
            self.settings.renders,
            self.settings.profile.namespace,
            'render',
            RENDER_MS,
        )

        if not bindings.TURNSTILE_SECRET:
            raise ValueError('Voting requires its challenge secret.')

    async def browser(self) -> FastAPI:
        if self.http is None:
            body = await self._asset('/arena-families.json')
            catalog = TypeAdapter(list[FamilyView]).validate_json(body)
            families = {family.name: family for family in catalog}

            if len(families) != len(catalog):
                raise ValueError('The prepared family catalog repeats a family.')

            self.http = application(
                BrowserApi(
                    self.auth,
                    Voting(
                        self.ledger,
                        self.settings.profile,
                        families,
                        self.pairs,
                        self.renders,
                    ),
                )
            )

        return self.http

    async def _asset(self, path: str) -> str:
        from workers import Request

        result = await self.bindings.ASSETS.fetch(
            Request(f'{self.settings.profile.app_origin}{path}').js_object
        )

        if result.status != 200:
            raise ValueError(f'The prepared asset `{path}` is unavailable.')

        body = await result.text()

        if len(body.encode()) > 1_048_576:
            raise ValueError(f'The prepared asset `{path}` exceeds its limit.')

        return body


_owner: Owner | None = None


def owner(bindings: AppBindings) -> Owner:
    global _owner

    if _owner is None:
        _owner = Owner(bindings)

    return _owner
