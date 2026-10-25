"""Authenticated pair acquisition and blinded views from sealed releases."""

import random
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, assert_never
from uuid import uuid4

from assistant_bench_arena.admission import TokenCodec
from assistant_bench_arena.api_models import PairView, SessionView
from assistant_bench_arena.ledger import Ledger
from assistant_bench_arena.ledger_models import (
    Context,
    Issue,
    Lease,
    Network,
    Publication,
)
from assistant_bench_arena.models import DiffPane, FamilyView, HtmlPane, Pane, TextPane
from assistant_bench_arena.read_models import ClaimState, Coverage, DueRepeat
from assistant_bench_arena.releases import EvaluationBundle
from assistant_bench_arena.runtime_models import PairClaims, Profile, RenderClaims
from assistant_bench_arena.scheduler import CandidatePool
from assistant_bench_arena.transport import CommandRejected


LEASE_MS = 7_200_000
RENDER_MS = 300_000


@dataclass(frozen=True)
class SealedFamily:
    evaluation: EvaluationBundle
    publication: Publication


class Voting:
    def __init__(
        self,
        ledger: Ledger,
        profile: Profile,
        catalog: Mapping[str, FamilyView],
        pairs: TokenCodec[PairClaims],
        renders: TokenCodec[RenderClaims],
    ) -> None:
        self.ledger = ledger
        self.profile = profile
        self.catalog = catalog
        self.pairs = pairs
        self.renders = renders
        self._sealed: dict[str, SealedFamily] = {}
        self._random = random.SystemRandom()

    async def start(
        self, context: Context, family: str, network: Network
    ) -> SessionView:
        self._listed(family)
        session = await self.ledger.open_session(
            context.principal, context.command_id, family, context.now
        )
        return await self.resume(context, session.id, network)

    async def resume(
        self, context: Context, session_id: str, network: Network
    ) -> SessionView:
        session = await self.ledger.read.session(context.principal, session_id)

        if session is None:
            raise CommandRejected('session_stale')

        self._listed(session.family)
        state = await self.ledger.read.family(session.family)

        if state is None:
            raise CommandRejected('family_missing')

        sealed = await self._release(
            session.family, state.evaluation_id, state.publication_id
        )
        pool = await self._pool(
            sealed.evaluation, context.principal, session_id, context.now
        )
        lease = await self.ledger.resume(context.principal, session_id, context.now)

        if lease is None:
            points = await self.ledger.read.points(state.point_id)
            selected = pool.draw(
                points,
                await self._coverage(session.family),
                sealed.evaluation.policy.method.points_per_logit,
                self._random,
            )

            if selected is not None:
                lease = await self.ledger.issue(
                    context,
                    Issue(
                        lease_id=str(uuid4()),
                        session_id=session_id,
                        session_version=session.version,
                        claim_id=selected.claim_id or str(uuid4()),
                        claim_version=selected.claim_version,
                        case_key=selected.case_key,
                        a_output=selected.a_output,
                        b_output=selected.b_output,
                        expires_at=context.now + LEASE_MS,
                        repeat_of=selected.repeat_of,
                        network=network,
                    ),
                )

        pending: frozenset[str] = (
            frozenset({lease.case_key}) if lease is not None else frozenset()
        )
        pair: PairView | None = None

        if lease is not None:
            sealed = await self._release(
                session.family, lease.evaluation_id, lease.publication_id
            )
            pair = await self._pair(lease, sealed.publication, context.now)

        presentation = sealed.publication.presentation
        return SessionView(
            id=session.id,
            family=FamilyView(
                name=presentation.family,
                aspect=presentation.aspect,
                display_name=presentation.display_name,
                defects=list(sealed.evaluation.evaluation.manifest.family.defects),
            ),
            votes=session.eligible_votes,
            remaining=len(pool.case_keys | pending),
            undo_acknowledgment=session.undo_acknowledgment,
            pair=pair,
        )

    def _listed(self, family: str) -> None:
        if family not in self.catalog:
            raise CommandRejected('family_missing')

    async def _release(
        self, family: str, evaluation_id: str, publication_id: str
    ) -> SealedFamily:
        previous = self._sealed.get(family)

        if (
            previous is not None
            and previous.evaluation.evaluation.id == evaluation_id
            and previous.publication.id == publication_id
        ):
            return previous

        sealed = SealedFamily(
            await self.ledger.read.evaluation(evaluation_id),
            await self.ledger.read.publication_metadata(publication_id),
        )
        self._sealed[family] = sealed
        return sealed

    async def _pool(
        self,
        evaluation: EvaluationBundle,
        actor_id: str,
        session_id: str,
        now: int,
    ) -> CandidatePool:
        family = evaluation.evaluation.family
        claims: list[ClaimState] = []
        claim_after = ''

        while True:
            page = await self.ledger.read.claims(family, actor_id, claim_after)
            claims.extend(page.rows)

            if page.after is None:
                break

            claim_after = page.after

        cases: set[str] = set()
        case_after = ''

        while True:
            used = await self.ledger.read.used_cases(actor_id, session_id, case_after)
            cases.update(used.rows)

            if used.after is None:
                break

            case_after = used.after

        repeats: list[DueRepeat] = []
        repeat_after = 0

        while True:
            due = await self.ledger.read.due_repeats(
                family, actor_id, now, repeat_after
            )
            repeats.extend(due.rows)

            if due.after is None:
                break

            repeat_after = due.after

        return CandidatePool.build(evaluation, claims, cases, repeats)

    async def _coverage(self, family: str) -> list[Coverage]:
        result: list[Coverage] = []
        after = ('', '')

        while True:
            page = await self.ledger.read.coverage(family, after)
            result.extend(page.rows)

            if page.after is None:
                return result

            after = page.after

    async def _pair(self, lease: Lease, publication: Publication, now: int) -> PairView:
        case = publication.presentation.cases[lease.case_key]
        return PairView(
            id=lease.id,
            capability=self.pairs.encode(
                PairClaims(
                    namespace=self.profile.namespace,
                    issued_at=now,
                    expires_at=lease.expires_at,
                    lease=lease.id,
                )
            ),
            instruction=case.instruction,
            checklist=list(case.checklist),
            a=await self._pane(lease, 'a', now),
            b=await self._pane(lease, 'b', now),
        )

    async def _pane(self, lease: Lease, side: Literal['a', 'b'], now: int) -> Pane:
        content = await self.ledger.read.render(lease.id, side, now)

        if content is None:
            raise CommandRejected('lease_stale')

        match content.render:
            case 'text':
                return TextPane(render='text', text=content.content)
            case 'diff':
                return DiffPane(render='diff', text=content.content)
            case 'html':
                capability = self.renders.encode(
                    RenderClaims(
                        namespace=self.profile.namespace,
                        issued_at=now,
                        expires_at=min(now + RENDER_MS, lease.expires_at),
                        lease=lease.id,
                        side=side,
                    )
                )
                return HtmlPane(
                    render='html',
                    url=f'{self.profile.render_origin}/render/{capability}',
                )
            case _:
                assert_never(content.render)
