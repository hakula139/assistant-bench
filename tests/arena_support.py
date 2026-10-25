import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from pydantic import TypeAdapter

from assistant_bench.arena.exports import FrozenExport, capture_export
from assistant_bench.arena.management import Management
from assistant_bench_arena import management_models as command
from assistant_bench_arena.ledger import Ledger
from assistant_bench_arena.ledger_models import (
    Accept,
    Admission,
    Attribution,
    BootstrapConfiguration,
    Context,
    Export,
    Issue,
    Quota,
    Session,
)
from assistant_bench_arena.management_domain import execute
from assistant_bench_arena.releases import Release
from assistant_bench_arena.serialization import canonical
from ledger_support import D1Runtime
from support import AT


BASE_MS = int(AT.timestamp() * 1_000)
RETENTION_MS = 60 * 86_400_000


class NativeManagement(Management):
    """Execute real private domain operations on D1 without the Service transport."""

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self.loop = asyncio.get_running_loop()
        self.now = BASE_MS
        self.operations: list[command.Request] = []
        self.lose_reply: str | None = None

    async def domain[Result](
        self, request: command.Request, adapter: TypeAdapter[Result]
    ) -> Result:
        if asyncio.get_running_loop() is not self.loop:
            return await asyncio.wrap_future(
                asyncio.run_coroutine_threadsafe(
                    self.domain(request, adapter), self.loop
                )
            )

        request = command.parse_request(canonical(request))
        self.operations.append(request)
        result = await execute(self.ledger, request, self.now)

        if self.lose_reply == request.operation:
            self.lose_reply = None
            raise ValueError('The management response was lost.')

        return adapter.validate_json(canonical(result))


@asynccontextmanager
async def native_management(
    runtime: D1Runtime,
) -> AsyncGenerator[NativeManagement]:
    async with runtime.client() as (ledger, transport):
        await transport.reset()
        await transport.bootstrap(
            BootstrapConfiguration(
                namespace='synthetic-management',
                quotas=(
                    Quota(operation='admit', scope='actor', window_ms=60_000, limit=10),
                    Quota(operation='issue', scope='actor', window_ms=60_000, limit=10),
                    Quota(
                        operation='accept', scope='actor', window_ms=60_000, limit=10
                    ),
                ),
                attribution_ms=RETENTION_MS,
            )
        )
        yield NativeManagement(ledger)


async def admit_actor(ledger: Ledger, actor_id: str, now: int) -> None:
    context = f'synthetic-admission:{actor_id}'
    await ledger.admit(
        Context(command_id=f'admit:{actor_id}', principal=context, now=now),
        Admission(
            operation='new',
            context_digest=context,
            proof_digest=f'synthetic-proof:{actor_id}',
            actor_id=actor_id,
            credential_id=f'credential:{actor_id}',
            rate_subject=f'browser:{actor_id}',
            expires_at=now + 60_000,
            challenge_until=now + 60_000,
        ),
    )


async def vote_capture(management: NativeManagement, release: Release) -> FrozenExport:
    ledger = management.ledger
    now = management.now
    await admit_actor(ledger, 'owner', now)
    await ledger.start_session(
        Session(
            id='session',
            actor_id='owner',
            family=release.evaluation.family,
            created_at=now,
        )
    )
    case = release.cases[0]
    outputs = [row for row in release.outputs if row.case_key == case.key]
    await ledger.issue(
        Context(command_id='draw', principal='owner', now=now + 1),
        Issue(
            lease_id='lease',
            session_id='session',
            session_version=0,
            claim_id='claim',
            claim_version=0,
            case_key=case.key,
            a_output=outputs[0].key,
            b_output=outputs[1].key,
            expires_at=now + 60_000,
        ),
    )
    await ledger.accept(
        Context(command_id='vote', principal='owner', now=now + 2),
        Accept(
            lease_id='lease',
            acknowledgment='ack',
            choice='a',
            attribution=(
                Attribution(
                    version='synthetic-key',
                    kind='exact',
                    tag='synthetic-exact-tag',
                    expires_at=now + 2 + RETENTION_MS,
                ),
                Attribution(
                    version='synthetic-key',
                    kind='prefix',
                    tag='synthetic-prefix-tag',
                    expires_at=now + 2 + RETENTION_MS,
                ),
            ),
        ),
    )
    management.now += 100
    header = await management.domain(
        command.Freeze(export_id='source', family=release.evaluation.family),
        TypeAdapter(Export),
    )
    return await capture_export(management, header)
