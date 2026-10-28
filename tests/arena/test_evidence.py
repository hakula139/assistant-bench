import asyncio
from collections.abc import Iterator
from typing import Literal

import pytest

from arena_support import BASE_MS, RETENTION_MS, native_management, vote_capture
from assistant_bench.arena.exports import capture_export
from assistant_bench.arena.publication import POLICY
from assistant_bench.arena.publishing import publish_release
from assistant_bench.arena.snapshots import analyze_export
from assistant_bench_arena.ledger import Ledger
from assistant_bench_arena.ledger_models import (
    Accept,
    Attribution,
    Context,
    Event,
    Issue,
    Moderation,
    Receipt,
    Session,
    Target,
)
from assistant_bench_arena.serialization import canonical
from ledger_fixtures import make_release
from ledger_support import D1Runtime


DAY_MS = 86_400_000


@pytest.fixture(scope='module')
def runtime(tmp_path_factory: pytest.TempPathFactory) -> Iterator[D1Runtime]:
    with D1Runtime(tmp_path_factory.mktemp('native-replay')).owner() as owner:
        yield owner


def attribution(now: int) -> tuple[Attribution, ...]:
    return tuple(
        Attribution(
            version='synthetic-key',
            kind=kind,
            tag=f'synthetic-{kind}',
            expires_at=now + RETENTION_MS,
        )
        for kind in ('exact', 'prefix')
    )


async def claim_version(ledger: Ledger, receipt: Receipt) -> int:
    claims = await ledger.read.claims(receipt.family, receipt.actor_id)
    return next(row.version for row in claims.rows if row.id == receipt.claim_id)


async def moderate(
    ledger: Ledger,
    receipt: Receipt,
    action: Literal['exclude', 'restore'],
    now: int,
    exclusion: int | None = None,
) -> Event:
    await ledger.stage_moderation(
        Moderation(
            id=action,
            family=receipt.family,
            principal='admin',
            action=action,
            reason='Synthetic reviewed moderation.',
            created_at=now,
            targets=(
                Target(
                    receipt_seq=receipt.seq,
                    claim_version=await claim_version(ledger, receipt),
                    exclusion_seq=exclusion,
                ),
            ),
        )
    )
    await ledger.seal_moderation(action, now)
    return await ledger.moderate(
        Context(command_id=action, principal='admin', now=now), action, 0
    )


class TestReplay:
    @pytest.mark.parametrize('history', ['selected', 'withdrawn', 'superseded'])
    def test_restoration_keeps_only_the_latest_eligible_receipt(
        self, runtime: D1Runtime, history: str
    ) -> None:
        async def scenario() -> None:
            async with native_management(runtime) as management:
                ledger = management.ledger
                release = make_release(cases=2, policy=POLICY)
                await publish_release(management, release)
                capture = await vote_capture(management, release)
                original = Receipt.model_validate_json(
                    canonical(capture.rows('receipts')[0])
                )
                now = BASE_MS + DAY_MS + 100
                await ledger.start_session(
                    Session(
                        id='repeat-session',
                        actor_id=original.actor_id,
                        family=original.family,
                        created_at=now,
                    )
                )
                lease = await ledger.issue(
                    Context(command_id='repeat-draw', principal='owner', now=now),
                    Issue(
                        lease_id='repeat-lease',
                        session_id='repeat-session',
                        session_version=0,
                        claim_id='repeat-claim',
                        claim_version=0,
                        case_key=original.case_key,
                        a_output=original.b_output,
                        b_output=original.a_output,
                        expires_at=now + 60_000,
                        repeat_of=original.seq,
                    ),
                )
                await ledger.accept(
                    Context(command_id='repeat-vote', principal='owner', now=now + 1),
                    Accept(
                        lease_id=lease.id,
                        acknowledgment='repeat',
                        choice='b',
                        attribution=attribution(now + 1),
                    ),
                )

                if history == 'withdrawn':
                    session = await ledger.read.session('owner', original.session_id)
                    assert session is not None
                    await ledger.withdraw(
                        Context(command_id='withdraw', principal='owner', now=now + 2),
                        session.id,
                        session.version,
                        original.seq,
                    )

                excluded = await moderate(ledger, original, 'exclude', now + 3)
                latest = original

                if history == 'superseded':
                    await ledger.start_session(
                        Session(
                            id='replacement-session',
                            actor_id='owner',
                            family=original.family,
                            created_at=now + 4,
                        )
                    )
                    replacement = await ledger.issue(
                        Context(
                            command_id='replacement-draw',
                            principal='owner',
                            now=now + 4,
                        ),
                        Issue(
                            lease_id='replacement-lease',
                            session_id='replacement-session',
                            session_version=0,
                            claim_id=original.claim_id,
                            claim_version=await claim_version(ledger, original),
                            case_key=original.case_key,
                            a_output=original.a_output,
                            b_output=original.b_output,
                            expires_at=now + 60_000,
                        ),
                    )
                    latest = await ledger.accept(
                        Context(
                            command_id='replacement-vote',
                            principal='owner',
                            now=now + 5,
                        ),
                        Accept(
                            lease_id=replacement.id,
                            acknowledgment='replacement',
                            choice='b',
                            attribution=attribution(now + 5),
                        ),
                    )

                await moderate(ledger, original, 'restore', now + 6, excluded.seq)
                header = await ledger.freeze('restored', original.family, now + 7)

                snapshot = analyze_export(await capture_export(ledger, header))

                assert [
                    (row.receipt, row.choice, row.comparison.weight)
                    for row in snapshot.analysis.ordinary
                ] == (
                    []
                    if history == 'withdrawn'
                    else [(str(latest.seq), latest.choice, 1.0)]
                )
                assert snapshot.analysis.accepted == (
                    3 if history == 'superseded' else 2
                )

        asyncio.run(scenario())
