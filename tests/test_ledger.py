import asyncio
import json
from collections.abc import Iterator
from datetime import datetime
from typing import get_args

import pytest
from pydantic import TypeAdapter

from assistant_bench_arena.ledger import CommandRejected, Ledger
from assistant_bench_arena.ledger_models import (
    Accept,
    Actor,
    Attribution,
    Case,
    Context,
    Evaluation,
    ExportSection,
    ImportAnnotation,
    ImportDisposition,
    ImportObservation,
    ImportPlan,
    ImportRow,
    ImportSelection,
    ImportSession,
    ImportSource,
    Issue,
    Lease,
    LegacySession,
    LegacyVote,
    LegacyWithdrawal,
    Mode,
    Moderation,
    Network,
    NetworkTag,
    Output,
    Point,
    PointSnapshot,
    Publication,
    Quota,
    ReceiptAnnotation,
    RenderedOutput,
    Row,
    Session,
    Statement,
    System,
    Target,
)
from assistant_bench_arena.serialization import digest, source_identity
from assistant_bench_arena.snapshots import (
    AnalysisPolicy,
    CappedHumanWeights,
    CaseApproval,
    CaseContentApproval,
    CaseMetadata,
    EvaluationManifest,
    FamilyDefinition,
    OutputApproval,
    OutputContentApproval,
    OutputMetadata,
    Presentation,
    PresentedCase,
    PublicApproval,
    SettledWeights,
    UnavailableInference,
    VotesMethod,
    VotesRules,
)
from ledger_support import D1Runtime, D1Transport


RETENTION_MS = 90 * 86_400_000
LEGACY_TIME = '2026-01-01T00:00:00+00:00'
LEGACY_MS = int(datetime.fromisoformat(LEGACY_TIME).timestamp() * 1_000)
IMPORT_MS = LEGACY_MS + 86_400_000
POLICY = AnalysisPolicy(
    method=VotesMethod(
        name='bradley-terry',
        version=1,
        l2_penalty=0.01,
        points_per_logit=100.0,
        anchor_score=1_000.0,
        human_weights=CappedHumanWeights(
            name='actor-proportional-cap',
            version=1,
            cap=100,
            scope='family-evaluation',
            repeat_handling='excluded',
        ),
        settled_weights=SettledWeights(name='sample-pair-share', version=1),
    ),
    rules=VotesRules(
        preliminary_decisive_votes=10,
        preliminary_decisive_vote_unit='raw-votes',
        rater_agreement_floor=0.5,
    ),
    inference=UnavailableInference(
        status='unavailable',
        target='new-cases-and-voters',
        reason='population-method-not-validated',
    ),
)
SECTIONS = TypeAdapter(tuple[ExportSection, ...]).validate_python(
    get_args(ExportSection.__value__)
)


async def export_rows(
    ledger: Ledger, export_id: str, *, limit: int
) -> dict[str, list[Row]]:
    result: dict[str, list[Row]] = {}

    for section in SECTIONS:
        rows: list[Row] = []
        cursor = None
        seen: set[str] = set()

        while True:
            page = await ledger.page(export_id, section, cursor, limit=limit)
            rows.extend(page.rows)

            if page.cursor is None:
                break

            assert page.cursor not in seen
            seen.add(page.cursor)
            cursor = page.cursor

        result[section] = rows

    return result


def legacy_vote(original_id: int) -> LegacyVote:
    return LegacyVote(
        id=original_id,
        session='legacy-session',
        case_id='original-case',
        a_system='large',
        a_fingerprint='fingerprint:large',
        a_job='synthetic-private-job',
        a_trial='original-large-trial',
        b_system='small',
        b_fingerprint='fingerprint:small',
        b_job='synthetic-private-job',
        b_trial='original-small-trial',
        choice='a',
        a_defects='[]',
        b_defects='[]',
        duration_sec=1.5,
        repeat_of=None,
        recorded_at=LEGACY_TIME,
    )


async def legacy_plan(
    ledger: Ledger,
    transport: D1Transport,
    *,
    receipts: int = 2,
    withdrawals: tuple[int, ...] = (),
) -> ImportPlan:
    await ledger.register_actor(
        Actor(id='owner', created_at=2, owner_namespace='legacy-owner')
    )
    await ledger.register_import(
        ImportSource(
            id='source',
            family='writing',
            owner_id='owner',
            source_schema='2',
            file_digest=digest('synthetic-source'),
            sessions=1,
            receipts=receipts,
            withdrawals=len(withdrawals),
        )
    )
    session = LegacySession(
        id='legacy-session', family='writing', started_at=LEGACY_TIME
    )
    await ledger.preserve_import(
        'source',
        ImportRow(
            kind='session',
            original_id=session.id,
            source_hash=digest(session),
            raw=session,
        ),
    )
    observations = []

    for original_id in range(1, receipts + 1):
        raw = legacy_vote(original_id)
        await ledger.preserve_import(
            'source',
            ImportRow(
                kind='receipt',
                original_id=str(original_id),
                source_hash=digest(raw),
                raw=raw,
            ),
        )
        rows = await transport.rows(
            "SELECT seq FROM import_rows WHERE source_id = 'source' "
            "AND row_kind = 'receipt' AND original_id = ?",
            str(original_id),
        )
        observations.append(
            ImportObservation(
                raw_seq=TypeAdapter(int).validate_python(rows[0]['seq']),
                raw_hash=digest(raw),
                command_id=f'legacy:{original_id}',
                acknowledgment=f'legacy-ack:{original_id}',
                claim_id='legacy-claim',
                proof_evaluation_id='evaluation:1',
                case_key='case:0',
                a_output='case:0:large',
                b_output='case:0:small',
                a_source_digest=source_identity(
                    'case:0', 'large', 'fingerprint:large', raw.a_job, raw.a_trial
                ),
                b_source_digest=source_identity(
                    'case:0', 'small', 'fingerprint:small', raw.b_job, raw.b_trial
                ),
                recorded_at=LEGACY_MS,
            )
        )

    for original_id in withdrawals:
        undo = LegacyWithdrawal(vote=original_id, undone_at=LEGACY_TIME)
        await ledger.preserve_import(
            'source',
            ImportRow(
                kind='withdrawal',
                original_id=str(original_id),
                source_hash=digest(undo),
                raw=undo,
            ),
        )

    state = await transport.rows(
        "SELECT revision FROM family_state WHERE family = 'writing'"
    )
    winner = next(
        (
            f'legacy-ack:{original_id}'
            for original_id in reversed(range(1, receipts + 1))
            if original_id not in withdrawals
        ),
        None,
    )
    return ImportPlan(
        id='import-plan',
        source_id='source',
        revision=TypeAdapter(int).validate_python(state[0]['revision']),
        initial=True,
        sessions=(
            ImportSession(
                original_id=session.id,
                session_id='imported-session',
                created_at=LEGACY_MS,
            ),
        ),
        observations=tuple(observations),
        selections=(
            ImportSelection(
                claim_id='legacy-claim',
                expected_version=0,
                expected_receipt=None,
                selected_acknowledgment=winner,
            ),
        ),
        dispositions=tuple(
            ImportDisposition(raw_seq=row.raw_seq, state='linked', reason='proved')
            for row in observations
        ),
    )


def attribution(now: int) -> tuple[Attribution, ...]:
    return tuple(
        Attribution(
            version='test-key',
            kind=kind,
            tag=f'attribution:{kind}',
            expires_at=now + RETENTION_MS,
        )
        for kind in ('exact', 'prefix')
    )


@pytest.fixture(scope='session')
def d1_runtime(tmp_path_factory: pytest.TempPathFactory) -> Iterator[D1Runtime]:
    with D1Runtime(tmp_path_factory.mktemp('ledger-runtime')).owner() as runtime:
        yield runtime


async def publish(
    ledger: Ledger,
    *,
    suffix: str = '1',
    family: str = 'writing',
    cases: int = 3,
) -> None:
    await ledger.stage_policy('policy', POLICY)
    await ledger.stage_evaluation(
        Evaluation(
            id=f'evaluation:{suffix}',
            family=family,
            policy_id='policy',
            manifest=EvaluationManifest(
                family=FamilyDefinition(
                    name=family,
                    reference='large',
                    systems=('large', 'small'),
                    categories=(),
                    defects=('tone',),
                ),
                policy=POLICY,
            ),
            cases=cases,
            systems=2,
            outputs=cases * 2,
            numerics=0,
        )
    )

    for index, name in enumerate(('large', 'small')):
        await ledger.stage_system(
            f'evaluation:{suffix}',
            System(id=name, fingerprint=f'fingerprint:{name}', order=index),
        )

    for index in range(cases):
        case = f'case:{index}'
        document = case_document(index)
        await ledger.stage_case(
            f'evaluation:{suffix}',
            Case(
                key=case,
                source='synthetic',
                instruction_digest=digest(document.instruction),
                metadata=CaseMetadata(
                    source_digest=digest({'instruction': document.instruction}),
                    approval=CaseContentApproval(
                        case_key=case,
                        source='synthetic',
                        attestation='Synthetic fixture content.',
                        instruction_digest=digest(document.instruction),
                    ),
                ),
            ),
        )

        for name in ('large', 'small'):
            content = output_content(index, name)
            await ledger.stage_output(
                f'evaluation:{suffix}',
                Output(
                    key=f'{case}:{name}',
                    case_key=case,
                    system_id=name,
                    content_digest=digest(content),
                    verdict='pass',
                    length=len(content),
                    approved_public=True,
                    metadata=OutputMetadata(
                        source_digest=source_identity(
                            case,
                            name,
                            f'fingerprint:{name}',
                            'synthetic-private-job',
                            f'original-{name}-trial',
                        ),
                        errored=False,
                        reward=1.0,
                        cost_usd=None,
                        latency_sec=None,
                        output_tokens=None,
                        approval=OutputContentApproval(
                            output_key=f'{case}:{name}', content_digest=digest(content)
                        ),
                    ),
                ),
            )

    await ledger.seal_evaluation(f'evaluation:{suffix}', 0)
    await publish_presentation(
        ledger,
        suffix=suffix,
        evaluation_id=f'evaluation:{suffix}',
        cases=cases,
        family=family,
    )


def case_document(index: int) -> PresentedCase:
    return PresentedCase(
        instruction=f'Synthetic instruction for case {index}.',
        checklist=('Use the requested tone.',),
    )


def output_content(index: int, name: str) -> str:
    return f'Output for case {index} from {name}.'


async def publish_presentation(
    ledger: Ledger,
    *,
    suffix: str,
    evaluation_id: str,
    cases: int = 3,
    family: str = 'writing',
) -> None:
    await ledger.stage_publication(
        Publication(
            id=f'publication:{suffix}',
            evaluation_id=evaluation_id,
            presentation=Presentation(
                family=family,
                aspect=None,
                display_name=family,
                render='text',
                systems={'large': 'Large', 'small': 'Small'},
                cases={f'case:{index}': case_document(index) for index in range(cases)},
            ),
            approval=PublicApproval(
                family=family,
                cases=tuple(
                    CaseApproval(
                        case_key=f'case:{index}',
                        source='synthetic',
                        attestation='Synthetic fixture content.',
                        instruction_digest=digest(case_document(index).instruction),
                        presentation_digest=digest(case_document(index)),
                    )
                    for index in range(cases)
                ),
                outputs=tuple(
                    OutputApproval(
                        output_key=f'case:{index}:{name}',
                        content_digest=digest(output_content(index, name)),
                        render_digest=digest(
                            {'render': 'text', 'content': output_content(index, name)}
                        ),
                    )
                    for index in range(cases)
                    for name in ('large', 'small')
                ),
            ),
            outputs=cases * 2,
        )
    )

    for index in range(cases):
        for name in ('large', 'small'):
            await ledger.stage_rendered(
                f'publication:{suffix}',
                RenderedOutput(
                    output_key=f'case:{index}:{name}',
                    render='text',
                    content=output_content(index, name),
                    content_digest=digest(output_content(index, name)),
                    approved_public=True,
                ),
            )

    await ledger.seal_publication(f'publication:{suffix}', 0)
    await ledger.stage_points(
        PointSnapshot(
            id=f'points:{suffix}',
            evaluation_id=evaluation_id,
            policy_id='policy',
            points={
                name: Point(score=None, component=None) for name in ('large', 'small')
            },
        ),
        0,
    )


async def prepare(
    ledger: Ledger,
    transport: D1Transport,
    *,
    mode: Mode = 'local',
    quotas: tuple[Quota, ...] = (),
) -> None:
    await transport.reset()
    await ledger.initialize(
        'test-deployment',
        mode,
        quotas,
        attribution_ms=RETENTION_MS if mode == 'public' else None,
    )
    await publish(ledger)
    await ledger.activate(
        Context(command_id='activate:1', principal='admin', now=1),
        'writing',
        0,
        'evaluation:1',
        'publication:1',
        'points:1',
    )
    await ledger.register_actor(Actor(id='actor', created_at=2))
    await ledger.start_session(
        Session(id='session', actor_id='actor', family='writing', created_at=2)
    )


def issue(*, session: str = 'session', lease: str = 'lease', case: int = 0) -> Issue:
    return Issue(
        lease_id=lease,
        session_id=session,
        session_version=0,
        claim_id=f'claim:{session}:{case}',
        claim_version=0,
        case_key=f'case:{case}',
        a_output=f'case:{case}:large',
        b_output=f'case:{case}:small',
        expires_at=10_000,
    )


class TestLedger:
    class TestInitialize:
        def test_initializes_once_and_rejects_conflicting_identity(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await transport.reset()
                    await ledger.initialize('deployment', 'local', ())

                    await ledger.initialize('deployment', 'local', ())

                    assert await transport.rows('SELECT * FROM deployment') == [
                        {
                            'id': 1,
                            'namespace': 'deployment',
                            'mode': 'local',
                            'quota_body': '[]',
                            'attribution_ms': 0,
                            'schema_version': 1,
                        }
                    ]

                    with pytest.raises(CommandRejected, match='^conflict$'):
                        await ledger.initialize('different', 'local', ())

            asyncio.run(scenario())

    class TestActivate:
        def test_presentation_refresh_preserves_numerical_freshness_and_pending_lease(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await prepare(ledger, transport)
                    lease = await ledger.issue(
                        Context(command_id='issue', principal='actor', now=10), issue()
                    )
                    before = await ledger.freeze('before', 'writing', 20)
                    await publish_presentation(
                        ledger, suffix='next', evaluation_id='evaluation:1'
                    )

                    await ledger.activate(
                        Context(command_id='activate:next', principal='admin', now=30),
                        'writing',
                        before.family_revision,
                        'evaluation:1',
                        'publication:next',
                        'points:next',
                    )
                    after = await ledger.freeze('after', 'writing', 40)

                    assert after.analysis_sequence == before.analysis_sequence
                    assert after.family_revision > before.family_revision
                    assert after.evaluation_id == before.evaluation_id
                    assert after.policy == before.policy
                    assert after.publication_id == 'publication:next'
                    assert after.point_id == 'points:next'
                    assert await ledger.resume('actor', 'session', 41) == lease
                    assert await ledger.freeze('before', 'writing', 50) == before
                    assert (
                        before.namespace == 'test-deployment'
                        and before.schema_version == 1
                    )

                    accepted = await ledger.accept(
                        Context(command_id='accept', principal='actor', now=60),
                        Accept(lease_id=lease.id, acknowledgment='ack', choice='b'),
                    )

                    assert accepted.publication_id == 'publication:1'
                    assert accepted.point_id == 'points:1'
                    assert (
                        await ledger.freeze('voted', 'writing', 70)
                    ).analysis_sequence > after.analysis_sequence

            asyncio.run(scenario())

    class TestIssue:
        def test_concurrent_draws_resolve_to_one_durable_pending_lease(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with (
                    d1_runtime.client() as (ledger, transport),
                    d1_runtime.client() as (other, _),
                ):
                    await prepare(ledger, transport)

                    results = await asyncio.gather(
                        ledger.issue(
                            Context(command_id='draw:1', principal='actor', now=10),
                            issue(lease='lease:1'),
                        ),
                        other.issue(
                            Context(command_id='draw:2', principal='actor', now=10),
                            issue(lease='lease:2'),
                        ),
                        return_exceptions=True,
                    )

                    leases = [value for value in results if isinstance(value, Lease)]
                    assert leases and all(value.id == leases[0].id for value in leases)
                    assert all(
                        isinstance(value, Lease)
                        or isinstance(value, CommandRejected)
                        and value.reason in ('session_stale', 'pending_exists')
                        for value in results
                    )
                    assert await ledger.resume('actor', 'session', 11) == leases[0]
                    assert await transport.rows(
                        'SELECT id, status FROM pair_leases'
                    ) == [{'id': leases[0].id, 'status': 'pending'}]

            asyncio.run(scenario())

        @pytest.mark.parametrize(
            ('delay', 'swapped', 'actor', 'withdrawn', 'accepted'),
            [
                (86_400_000, True, 'actor', False, True),
                (86_399_999, True, 'actor', False, False),
                (86_400_000, False, 'actor', False, False),
                (86_400_000, True, 'other', False, False),
                (86_400_000, True, 'actor', True, False),
            ],
            ids=[
                'at-24-hours',
                'too-early',
                'original-orientation',
                'wrong-actor',
                'withdrawn-original',
            ],
        )
        def test_repeats_require_original_lineage_and_remain_diagnostic(
            self,
            d1_runtime: D1Runtime,
            delay: int,
            swapped: bool,
            actor: str,
            withdrawn: bool,
            accepted: bool,
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await prepare(ledger, transport)
                    primary_lease = await ledger.issue(
                        Context(command_id='draw', principal='actor', now=10), issue()
                    )
                    primary = await ledger.accept(
                        Context(command_id='vote', principal='actor', now=20),
                        Accept(
                            lease_id=primary_lease.id,
                            acknowledgment='primary',
                            choice='a',
                        ),
                    )

                    if withdrawn:
                        await ledger.withdraw(
                            Context(command_id='withdraw', principal='actor', now=30),
                            'session',
                            2,
                            primary.seq,
                        )

                    if actor != 'actor':
                        await ledger.register_actor(Actor(id=actor, created_at=30))

                    await ledger.start_session(
                        Session(
                            id='repeat-session',
                            actor_id=actor,
                            family='writing',
                            created_at=30,
                        )
                    )
                    now = 20 + delay
                    request = issue(
                        session='repeat-session', lease='repeat-lease'
                    ).model_copy(
                        update={
                            'claim_id': 'repeat-claim',
                            'a_output': 'case:0:small' if swapped else 'case:0:large',
                            'b_output': 'case:0:large' if swapped else 'case:0:small',
                            'expires_at': now + 10_000,
                            'repeat_of': primary.seq,
                        }
                    )
                    context = Context(
                        command_id='repeat-draw', principal=actor, now=now
                    )

                    if accepted:
                        repeated = await ledger.issue(context, request)
                        receipt = await ledger.accept(
                            Context(
                                command_id='repeat-vote', principal=actor, now=now + 1
                            ),
                            Accept(
                                lease_id=repeated.id,
                                acknowledgment='repeat',
                                choice='b',
                            ),
                        )

                        assert receipt.repeat_of == primary.seq
                        assert receipt.a_output == primary.b_output
                        assert receipt.b_output == primary.a_output
                        assert receipt.choice == 'b'
                    else:
                        with pytest.raises(
                            CommandRejected, match='^repeat_ineligible$'
                        ):
                            await ledger.issue(context, request)

                        assert (
                            await ledger.resume(actor, 'repeat-session', now + 1)
                            is None
                        )

                    assert await transport.rows(
                        'SELECT receipt_seq FROM eligible_claims'
                    ) == ([] if withdrawn else [{'receipt_seq': primary.seq}])
                    assert await transport.rows('SELECT count FROM coverage') == [
                        {'count': 0 if withdrawn else 1}
                    ]

            asyncio.run(scenario())

        @pytest.mark.parametrize('scope', ['actor', 'exact', 'prefix', 'deployment'])
        def test_expired_lease_keeps_every_issuance_budget_spent(
            self, d1_runtime: D1Runtime, scope: str
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await prepare(
                        ledger,
                        transport,
                        mode='public',
                        quotas=tuple(
                            Quota(
                                operation='issue',
                                scope=kind,
                                window_ms=1_000,
                                limit=1 if kind == scope else 100,
                            )
                            for kind in ('actor', 'exact', 'prefix', 'deployment')
                        ),
                    )
                    network = Network(
                        tags=(
                            NetworkTag(scope='exact', window_ms=1_000, tag='exact-tag'),
                            NetworkTag(
                                scope='prefix', window_ms=1_000, tag='prefix-tag'
                            ),
                        )
                    )
                    request = issue().model_copy(
                        update={'expires_at': 20, 'network': network}
                    )
                    first = await ledger.issue(
                        Context(command_id='draw', principal='actor', now=10), request
                    )

                    with pytest.raises(CommandRejected, match='^quota_exhausted$'):
                        await ledger.issue(
                            Context(command_id='next', principal='actor', now=30),
                            issue(lease='next', case=1).model_copy(
                                update={'session_version': 1, 'network': network}
                            ),
                        )

                    assert (
                        await ledger.issue(
                            Context(command_id='draw', principal='actor', now=31),
                            request,
                        )
                        == first
                    )
                    assert await ledger.resume('actor', 'session', 31) is None
                    assert await transport.rows('SELECT id FROM pair_leases') == [
                        {'id': first.id}
                    ]
                    assert await transport.rows('SELECT id FROM claims') == [
                        {'id': first.claim_id}
                    ]
                    assert {
                        row['scope']: row['consumed']
                        for row in await transport.rows(
                            'SELECT scope, consumed FROM quota_windows '
                            "WHERE operation = 'issue'"
                        )
                    } == dict.fromkeys(('actor', 'exact', 'prefix', 'deployment'), 1)

            asyncio.run(scenario())

    class TestAccept:
        def test_conflicting_concurrent_meanings_keep_the_winning_receipt(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with (
                    d1_runtime.client() as (ledger, transport),
                    d1_runtime.client() as (other, _),
                ):
                    await prepare(ledger, transport)
                    lease = await ledger.issue(
                        Context(command_id='draw', principal='actor', now=10), issue()
                    )
                    context = Context(command_id='vote', principal='actor', now=20)

                    results = await asyncio.gather(
                        ledger.accept(
                            context,
                            Accept(lease_id=lease.id, acknowledgment='ack', choice='a'),
                        ),
                        other.accept(
                            context,
                            Accept(lease_id=lease.id, acknowledgment='ack', choice='b'),
                        ),
                        return_exceptions=True,
                    )

                    rejected = [
                        value for value in results if isinstance(value, CommandRejected)
                    ]
                    winner = [
                        value
                        for value in results
                        if not isinstance(value, BaseException)
                    ]
                    assert len(rejected) == 1 and rejected[0].reason == 'conflict'
                    assert len(winner) == 1
                    assert await transport.rows(
                        'SELECT seq, choice FROM vote_receipts'
                    ) == [{'seq': winner[0].seq, 'choice': winner[0].choice}]
                    assert await transport.rows('SELECT count FROM coverage') == [
                        {'count': 1}
                    ]

            asyncio.run(scenario())

        def test_linked_actor_inherits_owner_quota_spent_in_another_family(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    quotas = (
                        Quota(
                            operation='issue', scope='actor', window_ms=1_000, limit=100
                        ),
                        Quota(
                            operation='accept', scope='actor', window_ms=1_000, limit=1
                        ),
                    )
                    await prepare(ledger, transport, mode='public', quotas=quotas)
                    await publish(ledger, suffix='other', family='other')
                    await ledger.activate(
                        Context(command_id='activate:other', principal='admin', now=2),
                        'other',
                        0,
                        'evaluation:other',
                        'publication:other',
                        'points:other',
                    )
                    await ledger.register_actor(
                        Actor(id='owner', created_at=2, owner_namespace='owner-source')
                    )
                    await ledger.start_session(
                        Session(
                            id='owner-session',
                            actor_id='owner',
                            family='other',
                            created_at=2,
                        )
                    )
                    owner_lease = await ledger.issue(
                        Context(command_id='issue:owner', principal='owner', now=10),
                        issue(session='owner-session', lease='owner-lease'),
                    )
                    await ledger.accept(
                        Context(command_id='accept:owner', principal='owner', now=20),
                        Accept(
                            lease_id=owner_lease.id,
                            acknowledgment='owner-ack',
                            choice='a',
                            attribution=attribution(20),
                        ),
                    )
                    plan = await ledger.prepare_attribution(
                        'link', 'writing', 'actor', 'owner'
                    )
                    await ledger.stage_attribution(plan)
                    await ledger.seal_attribution(plan.id, 30)
                    await ledger.attribute(
                        Context(command_id='link', principal='admin', now=30), plan.id
                    )
                    source_lease = await ledger.issue(
                        Context(command_id='issue:source', principal='actor', now=40),
                        issue(),
                    )

                    with pytest.raises(CommandRejected, match='^quota_exhausted$'):
                        await ledger.accept(
                            Context(
                                command_id='accept:source', principal='actor', now=50
                            ),
                            Accept(
                                lease_id=source_lease.id,
                                acknowledgment='source-ack',
                                choice='b',
                                attribution=attribution(50),
                            ),
                        )

                    assert await transport.rows(
                        'SELECT actor_id, family, choice FROM vote_receipts'
                    ) == [{'actor_id': 'owner', 'family': 'other', 'choice': 'a'}]
                    assert await ledger.resume('actor', 'session', 51) == source_lease

            asyncio.run(scenario())

        def test_concurrent_identical_commands_share_one_receipt(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with (
                    d1_runtime.client() as (ledger, transport),
                    d1_runtime.client() as (other, _),
                ):
                    await prepare(ledger, transport)
                    lease = await ledger.issue(
                        Context(command_id='issue', principal='actor', now=10), issue()
                    )
                    request = Accept(
                        lease_id=lease.id, acknowledgment='ack', choice='a'
                    )
                    context = Context(command_id='accept', principal='actor', now=20)

                    receipts = await asyncio.gather(
                        *(
                            client.accept(context, request)
                            for client in [ledger, other] * 6
                        )
                    )

                    assert all(receipt == receipts[0] for receipt in receipts)
                    assert await transport.rows(
                        'SELECT seq, choice FROM vote_receipts'
                    ) == [{'seq': receipts[0].seq, 'choice': 'a'}]
                    assert await transport.rows('SELECT count FROM coverage') == [
                        {'count': 1}
                    ]

            asyncio.run(scenario())

        @pytest.mark.parametrize(
            'window_ms', [1_000, 86_400_000], ids=['second', 'day']
        )
        def test_aligns_epoch_windows_at_boundaries_and_preserves_spent_units(
            self, d1_runtime: D1Runtime, window_ms: int
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await prepare(
                        ledger,
                        transport,
                        mode='public',
                        quotas=(
                            Quota(
                                operation='issue',
                                scope='actor',
                                window_ms=1_000,
                                limit=100,
                            ),
                            Quota(
                                operation='accept',
                                scope='actor',
                                window_ms=window_ms,
                                limit=1,
                            ),
                        ),
                    )
                    base = 1_728_000_000_000
                    receipts = []
                    requests = []

                    for index, now in enumerate(
                        (base + window_ms - 1, base + window_ms, base + window_ms + 1)
                    ):
                        lease = await ledger.issue(
                            Context(
                                command_id=f'issue:{index}',
                                principal='actor',
                                now=now - 1,
                            ),
                            issue(lease=f'lease:{index}', case=index).model_copy(
                                update={
                                    'session_version': index * 2,
                                    'expires_at': now + 10_000,
                                }
                            ),
                        )
                        context = Context(
                            command_id=f'accept:{index}', principal='actor', now=now
                        )
                        request = Accept(
                            lease_id=lease.id,
                            acknowledgment=f'ack:{index}',
                            choice='a',
                            attribution=attribution(now),
                        )

                        if index == 2:
                            with pytest.raises(
                                CommandRejected, match='^quota_exhausted$'
                            ):
                                await ledger.accept(context, request)
                        else:
                            receipts.append(await ledger.accept(context, request))
                            requests.append(request)

                    replay = await ledger.accept(
                        Context(
                            command_id='accept:0',
                            principal='actor',
                            now=base + 3 * window_ms,
                        ),
                        requests[0],
                    )
                    await ledger.withdraw(
                        Context(
                            command_id='withdraw',
                            principal='actor',
                            now=base + 3 * window_ms,
                        ),
                        'session',
                        5,
                        receipts[1].seq,
                    )

                    assert replay == receipts[0]
                    assert await transport.rows(
                        'SELECT window_start, consumed FROM quota_windows '
                        "WHERE operation = 'accept' ORDER BY window_start"
                    ) == [
                        {'window_start': base, 'consumed': 1},
                        {'window_start': base + window_ms, 'consumed': 1},
                    ]
                    assert (
                        await ledger.resume('actor', 'session', base + 3 * window_ms)
                        is None
                    )

            asyncio.run(scenario())

        def test_late_receipt_collision_rolls_back_the_complete_public_command(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await prepare(
                        ledger,
                        transport,
                        mode='public',
                        quotas=tuple(
                            Quota(
                                operation=operation,
                                scope='actor',
                                window_ms=1_000,
                                limit=100,
                            )
                            for operation in ('issue', 'accept')
                        ),
                    )
                    first = await ledger.issue(
                        Context(command_id='issue:1', principal='actor', now=10),
                        issue(),
                    )
                    await ledger.accept(
                        Context(command_id='accept:1', principal='actor', now=20),
                        Accept(
                            lease_id=first.id,
                            acknowledgment='collision',
                            choice='a',
                            attribution=attribution(20),
                        ),
                    )
                    second = await ledger.issue(
                        Context(command_id='issue:2', principal='actor', now=30),
                        issue(lease='next', case=1).model_copy(
                            update={'session_version': 2}
                        ),
                    )
                    tables = (
                        'evidence_events',
                        'vote_receipts',
                        'receipt_state',
                        'claims',
                        'pair_leases',
                        'session_state',
                        'session_cases',
                        'coverage',
                        'coverage_members',
                        'quota_windows',
                        'network_attribution',
                        'family_state',
                    )
                    before = [
                        await transport.rows(f'SELECT * FROM {table} ORDER BY rowid')
                        for table in tables
                    ]

                    with pytest.raises(CommandRejected, match='^immutable$'):
                        await ledger.accept(
                            Context(command_id='accept:2', principal='actor', now=40),
                            Accept(
                                lease_id=second.id,
                                acknowledgment='collision',
                                choice='b',
                                attribution=attribution(40),
                            ),
                        )

                    assert [
                        await transport.rows(f'SELECT * FROM {table} ORDER BY rowid')
                        for table in tables
                    ] == before
                    assert await ledger.resume('actor', 'session', 41) == second

            asyncio.run(scenario())

        @pytest.mark.parametrize('scope', ['actor', 'exact', 'prefix', 'deployment'])
        def test_each_accepted_quota_stays_spent_after_withdrawal(
            self, d1_runtime: D1Runtime, scope: str
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    quotas = (
                        Quota(
                            operation='issue', scope='actor', window_ms=1_000, limit=100
                        ),
                        *(
                            Quota(
                                operation='accept',
                                scope=kind,
                                window_ms=1_000,
                                limit=1 if kind == scope else 100,
                            )
                            for kind in ('actor', 'exact', 'prefix', 'deployment')
                        ),
                    )
                    network = Network(
                        tags=(
                            NetworkTag(scope='exact', window_ms=1_000, tag='exact-tag'),
                            NetworkTag(
                                scope='prefix', window_ms=1_000, tag='prefix-tag'
                            ),
                        )
                    )
                    await prepare(ledger, transport, mode='public', quotas=quotas)
                    lease = await ledger.issue(
                        Context(command_id='issue:1', principal='actor', now=10),
                        issue(),
                    )
                    receipt = await ledger.accept(
                        Context(command_id='accept:1', principal='actor', now=20),
                        Accept(
                            lease_id=lease.id,
                            acknowledgment='ack:1',
                            choice='a',
                            network=network,
                            attribution=attribution(20),
                        ),
                    )
                    assert await transport.rows(
                        'SELECT DISTINCT window_start FROM quota_windows '
                        "WHERE operation = 'accept'"
                    ) == [{'window_start': 0}]

                    await ledger.withdraw(
                        Context(command_id='withdraw', principal='actor', now=30),
                        'session',
                        2,
                        receipt.seq,
                    )
                    successor = await ledger.issue(
                        Context(command_id='issue:2', principal='actor', now=40),
                        issue(lease='next', case=1).model_copy(
                            update={'session_version': 3}
                        ),
                    )

                    with pytest.raises(CommandRejected, match='^quota_exhausted$'):
                        await ledger.accept(
                            Context(command_id='accept:2', principal='actor', now=50),
                            Accept(
                                lease_id=successor.id,
                                acknowledgment='ack:2',
                                choice='b',
                                network=network,
                                attribution=attribution(50),
                            ),
                        )

                    windows = await transport.rows(
                        'SELECT scope, consumed FROM quota_windows '
                        "WHERE operation = 'accept'"
                    )
                    assert {row['scope']: row['consumed'] for row in windows} == {
                        'actor': 1,
                        'exact': 1,
                        'prefix': 1,
                        'deployment': 1,
                    }
                    assert await transport.rows('SELECT choice FROM vote_receipts') == [
                        {'choice': 'a'}
                    ]
                    assert await ledger.resume('actor', 'session', 51) == successor

            asyncio.run(scenario())

        def test_replays_after_expiry_and_retains_one_receipt(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await prepare(ledger, transport)
                    lease = await ledger.issue(
                        Context(command_id='issue', principal='actor', now=10), issue()
                    )
                    request = Accept(
                        lease_id=lease.id, acknowledgment='ack', choice='a'
                    )

                    accepted = await ledger.accept(
                        Context(command_id='accept', principal='actor', now=20), request
                    )
                    replay = await ledger.accept(
                        Context(command_id='accept', principal='actor', now=20_000),
                        request,
                    )

                    with pytest.raises(CommandRejected, match='^candidate_stale$'):
                        await ledger.issue(
                            Context(
                                command_id='successor', principal='actor', now=20_001
                            ),
                            issue(lease='successor', case=99).model_copy(
                                update={
                                    'session_version': 2,
                                    'expires_at': 30_000,
                                }
                            ),
                        )

                    assert (
                        await ledger.accept(
                            Context(command_id='accept', principal='actor', now=20_002),
                            request,
                        )
                        == accepted
                    )
                    assert replay == accepted
                    assert accepted.duration_ms == 10
                    assert accepted.a_system == 'large' and accepted.b_system == 'small'
                    assert await ledger.resume('actor', 'session', 21) is None
                    assert await transport.rows('SELECT * FROM coverage') == [
                        {
                            'family': 'writing',
                            'a_system': 'large',
                            'b_system': 'small',
                            'count': 1,
                        }
                    ]

            asyncio.run(scenario())

        @pytest.mark.parametrize(
            'sql',
            [
                "UPDATE vote_receipts SET choice = 'b'",
                'DELETE FROM vote_receipts',
                'INSERT OR REPLACE INTO vote_receipts SELECT * FROM vote_receipts',
                "UPDATE evidence_events SET principal = 'other' WHERE kind = 'accept'",
                "DELETE FROM evidence_events WHERE kind = 'accept'",
                """
                    INSERT OR REPLACE INTO evidence_events(command_id, kind, family,
                        principal, request_hash, created_at, data)
                    SELECT command_id, kind, family, 'other', request_hash,
                        created_at, data FROM evidence_events WHERE kind = 'accept'
                """,
            ],
            ids=[
                'receipt-update',
                'receipt-delete',
                'receipt-replace',
                'event-update',
                'event-delete',
                'event-command-replace',
            ],
        )
        def test_receipts_and_events_reject_mutation_and_replacement(
            self, d1_runtime: D1Runtime, sql: str
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await prepare(ledger, transport)
                    lease = await ledger.issue(
                        Context(command_id='draw', principal='actor', now=10), issue()
                    )
                    receipt = await ledger.accept(
                        Context(command_id='vote', principal='actor', now=20),
                        Accept(lease_id=lease.id, acknowledgment='ack', choice='a'),
                    )
                    tables = (
                        'vote_receipts',
                        'evidence_events',
                        'receipt_state',
                        'coverage',
                    )
                    before = [
                        await transport.rows(f'SELECT * FROM {table} ORDER BY rowid')
                        for table in tables
                    ]

                    with pytest.raises(CommandRejected, match='^immutable$'):
                        await transport.execute(Statement(sql=sql))

                    assert [
                        await transport.rows(f'SELECT * FROM {table} ORDER BY rowid')
                        for table in tables
                    ] == before
                    assert (
                        await ledger.accept(
                            Context(command_id='vote', principal='actor', now=21),
                            Accept(lease_id=lease.id, acknowledgment='ack', choice='a'),
                        )
                        == receipt
                    )

            asyncio.run(scenario())

    class TestWithdraw:
        def test_withdrawal_racing_a_repeat_acceptance_leaves_no_primary_coverage(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with (
                    d1_runtime.client() as (ledger, transport),
                    d1_runtime.client() as (other, _),
                ):
                    await prepare(ledger, transport)
                    primary_lease = await ledger.issue(
                        Context(command_id='draw', principal='actor', now=10), issue()
                    )
                    primary = await ledger.accept(
                        Context(command_id='vote', principal='actor', now=20),
                        Accept(
                            lease_id=primary_lease.id,
                            acknowledgment='primary',
                            choice='a',
                        ),
                    )
                    now = 86_400_020
                    await ledger.start_session(
                        Session(
                            id='repeat-session',
                            actor_id='actor',
                            family='writing',
                            created_at=30,
                        )
                    )
                    repeated = await ledger.issue(
                        Context(command_id='repeat-draw', principal='actor', now=now),
                        issue(session='repeat-session', lease='repeat').model_copy(
                            update={
                                'claim_id': 'repeat-claim',
                                'a_output': primary.b_output,
                                'b_output': primary.a_output,
                                'expires_at': now + 1_000,
                                'repeat_of': primary.seq,
                            }
                        ),
                    )

                    withdrawn, accepted = await asyncio.gather(
                        ledger.withdraw(
                            Context(
                                command_id='withdraw', principal='actor', now=now + 1
                            ),
                            'session',
                            2,
                            primary.seq,
                        ),
                        other.accept(
                            Context(
                                command_id='repeat-vote', principal='actor', now=now + 1
                            ),
                            Accept(
                                lease_id=repeated.id,
                                acknowledgment='repeat',
                                choice='b',
                            ),
                        ),
                        return_exceptions=True,
                    )

                    assert not isinstance(withdrawn, BaseException)

                    if isinstance(accepted, BaseException):
                        assert isinstance(accepted, CommandRejected)
                        assert accepted.reason in ('lease_stale', 'repeat_ineligible')
                    else:
                        assert accepted.repeat_of == primary.seq
                        assert accepted.a_output == primary.b_output
                        assert accepted.b_output == primary.a_output

                    assert await transport.rows(
                        'SELECT withdrawn FROM receipt_state WHERE receipt_seq = ?',
                        primary.seq,
                    ) == [{'withdrawn': withdrawn.seq}]
                    assert await transport.rows('SELECT * FROM eligible_claims') == []
                    assert await transport.rows('SELECT count FROM coverage') == [
                        {'count': 0}
                    ]
                    assert (
                        await ledger.resume('actor', 'repeat-session', now + 2) is None
                    )

            asyncio.run(scenario())

    class TestAttribute:
        def test_merges_latest_choices_and_unlink_does_not_revive_older_votes(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await prepare(ledger, transport)
                    await ledger.register_actor(
                        Actor(
                            id='owner', created_at=2, owner_namespace='registered-owner'
                        )
                    )
                    await ledger.start_session(
                        Session(
                            id='owner-session',
                            actor_id='owner',
                            family='writing',
                            created_at=2,
                        )
                    )
                    owner_lease = await ledger.issue(
                        Context(command_id='owner-draw', principal='owner', now=10),
                        issue(session='owner-session', lease='owner-lease'),
                    )
                    old = await ledger.accept(
                        Context(command_id='owner-vote', principal='owner', now=20),
                        Accept(
                            lease_id=owner_lease.id,
                            acknowledgment='owner-ack',
                            choice='a',
                        ),
                    )
                    source_lease = await ledger.issue(
                        Context(command_id='source-draw', principal='actor', now=30),
                        issue(),
                    )
                    latest = await ledger.accept(
                        Context(command_id='source-vote', principal='actor', now=40),
                        Accept(
                            lease_id=source_lease.id,
                            acknowledgment='source-ack',
                            choice='b',
                        ),
                    )
                    plan = await ledger.prepare_attribution(
                        'link', 'writing', 'actor', 'owner'
                    )
                    await ledger.stage_attribution(plan)
                    await ledger.seal_attribution(plan.id, 50)

                    linked = await ledger.attribute(
                        Context(command_id='link', principal='admin', now=60), plan.id
                    )

                    assert await transport.rows(
                        'SELECT receipt_seq FROM eligible_claims'
                    ) == [{'receipt_seq': latest.seq}]
                    assert await transport.rows('SELECT count FROM coverage') == [
                        {'count': 1}
                    ]
                    assert await transport.rows(
                        'SELECT superseded FROM receipt_state WHERE receipt_seq = ?',
                        old.seq,
                    ) == [{'superseded': linked.seq}]
                    assert (
                        await ledger.attribute(
                            Context(command_id='link', principal='admin', now=61),
                            plan.id,
                        )
                        == linked
                    )

                    undo = await ledger.prepare_attribution(
                        'unlink', 'writing', 'actor', 'actor'
                    )
                    await ledger.stage_attribution(undo)
                    await ledger.seal_attribution(undo.id, 70)
                    await ledger.attribute(
                        Context(command_id='unlink', principal='admin', now=80), undo.id
                    )

                    assert await transport.rows(
                        'SELECT receipt_seq FROM eligible_claims'
                    ) == [{'receipt_seq': latest.seq}]
                    assert await transport.rows('SELECT count FROM coverage') == [
                        {'count': 1}
                    ]
                    assert await transport.rows(
                        'SELECT superseded FROM receipt_state WHERE receipt_seq = ?',
                        old.seq,
                    ) == [{'superseded': linked.seq}]
                    assert await transport.rows(
                        'SELECT canonical_actor FROM actor_resolution '
                        "WHERE actor_id = 'actor'"
                    ) == [{'canonical_actor': 'actor'}]

            asyncio.run(scenario())

        def test_new_pending_claim_invalidates_a_reviewed_link_plan(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await prepare(ledger, transport)
                    await ledger.register_actor(
                        Actor(
                            id='owner', created_at=2, owner_namespace='registered-owner'
                        )
                    )
                    plan = await ledger.prepare_attribution(
                        'link', 'writing', 'actor', 'owner'
                    )
                    await ledger.stage_attribution(plan)
                    await ledger.seal_attribution(plan.id, 10)
                    lease = await ledger.issue(
                        Context(command_id='draw', principal='actor', now=20), issue()
                    )
                    before = await transport.rows(
                        'SELECT * FROM actor_resolution ORDER BY actor_id'
                    )

                    with pytest.raises(CommandRejected, match='^attribution_stale$'):
                        await ledger.attribute(
                            Context(command_id='link', principal='admin', now=30),
                            plan.id,
                        )

                    assert (
                        await transport.rows(
                            'SELECT * FROM actor_resolution ORDER BY actor_id'
                        )
                        == before
                    )
                    assert await ledger.resume('actor', 'session', 31) == lease
                    assert (
                        await transport.rows(
                            "SELECT * FROM evidence_events WHERE command_id = 'link'"
                        )
                        == []
                    )

            asyncio.run(scenario())

    class TestPreserveImport:
        def test_original_identity_replay_rejects_a_changed_receipt(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await prepare(ledger, transport)
                    await legacy_plan(ledger, transport, receipts=1)
                    before = await transport.rows(
                        'SELECT * FROM import_rows ORDER BY seq'
                    )
                    changed = legacy_vote(1).model_copy(update={'choice': 'b'})

                    with pytest.raises(CommandRejected, match='^conflict$'):
                        await ledger.preserve_import(
                            'source',
                            ImportRow(
                                kind='receipt',
                                original_id='1',
                                source_hash=digest(changed),
                                raw=changed,
                            ),
                        )

                    assert (
                        await transport.rows('SELECT * FROM import_rows ORDER BY seq')
                        == before
                    )
                    assert await transport.rows('SELECT * FROM vote_receipts') == []
                    assert await transport.rows('SELECT * FROM coverage_members') == []

            asyncio.run(scenario())

        def test_native_raw_storage_rejects_private_notes(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await prepare(ledger, transport)
                    await legacy_plan(ledger, transport, receipts=1)
                    before = await transport.rows(
                        'SELECT * FROM import_rows ORDER BY seq'
                    )
                    raw = legacy_vote(2).model_dump(mode='json')
                    raw['note'] = 'Synthetic private note.'

                    with pytest.raises(CommandRejected, match='^import_invalid$'):
                        await transport.execute(
                            Statement(
                                sql="""
                                INSERT INTO import_rows(source_id, row_kind,
                                    original_id, row_hash, raw, initial_disposition)
                                VALUES (?, ?, ?, ?, ?, ?)
                            """,
                                params=(
                                    'source',
                                    'receipt',
                                    '2',
                                    digest(raw),
                                    json.dumps(raw),
                                    'unresolved',
                                ),
                            )
                        )

                    assert (
                        await transport.rows('SELECT * FROM import_rows ORDER BY seq')
                        == before
                    )
                    assert (
                        await transport.rows('SELECT * FROM private_annotations') == []
                    )

            asyncio.run(scenario())

    class TestApplyImport:
        @pytest.mark.parametrize(
            'withdrawals', [(), (2,)], ids=['latest', 'latest-undone']
        )
        def test_import_preserves_original_undo_and_honest_legacy_provenance(
            self, d1_runtime: D1Runtime, withdrawals: tuple[int, ...]
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await prepare(ledger, transport)
                    plan = await legacy_plan(ledger, transport, withdrawals=withdrawals)
                    await ledger.stage_import(plan)
                    await ledger.seal_import(plan.id, IMPORT_MS)
                    context = Context(
                        command_id='import', principal='admin', now=IMPORT_MS
                    )

                    applied = await ledger.apply_import(context, plan.id)
                    replay = await ledger.apply_import(
                        context.model_copy(update={'now': IMPORT_MS + 10}), plan.id
                    )
                    frozen = await ledger.freeze('imported', 'writing', IMPORT_MS + 20)
                    rows = await export_rows(ledger, frozen.id, limit=1)

                    assert replay == applied
                    assert await transport.rows(
                        'SELECT r.original_id FROM eligible_claims c '
                        'JOIN vote_receipts r ON r.seq = c.receipt_seq'
                    ) == [{'original_id': '1' if withdrawals else '2'}]
                    assert await transport.rows('SELECT count FROM coverage') == [
                        {'count': 1}
                    ]
                    assert sorted(
                        TypeAdapter(list[str]).validate_python(
                            [row['original_id'] for row in rows['receipts']]
                        )
                    ) == ['1', '2']

                    for receipt in rows['receipts']:
                        assert receipt['origin'] == 'legacy'
                        assert receipt['import_source'] == 'source'
                        assert receipt['proof_evaluation_id'] == 'evaluation:1'
                        assert receipt['recorded_at'] == LEGACY_MS
                        assert receipt['legacy_duration_sec'] == 1.5
                        assert all(
                            receipt[field] is None
                            for field in (
                                'lease_id',
                                'claim_version',
                                'evaluation_id',
                                'publication_id',
                                'point_id',
                                'duration_ms',
                            )
                        )

                    assert await transport.rows('SELECT * FROM pair_leases') == []
                    assert await transport.rows('SELECT * FROM quota_windows') == []
                    assert await transport.rows(
                        'SELECT r.original_id FROM receipt_state s '
                        'JOIN vote_receipts r ON r.seq = s.receipt_seq '
                        'WHERE s.withdrawn IS NOT NULL'
                    ) == ([{'original_id': '2'}] if withdrawals else [])
                    assert 'synthetic-private-job' not in json.dumps(rows)
                    assert await transport.rows('SELECT * FROM import_execution') == []

            asyncio.run(scenario())

        def test_late_observation_failure_rolls_back_the_entire_reviewed_plan(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await prepare(ledger, transport)
                    lease = await ledger.issue(
                        Context(command_id='draw', principal='actor', now=10), issue()
                    )
                    await ledger.accept(
                        Context(command_id='vote', principal='actor', now=20),
                        Accept(
                            lease_id=lease.id, acknowledgment='collision', choice='a'
                        ),
                    )
                    good = await legacy_plan(ledger, transport)
                    bad = good.model_copy(
                        update={
                            'observations': (
                                good.observations[0],
                                good.observations[1].model_copy(
                                    update={'acknowledgment': 'collision'}
                                ),
                            ),
                            'selections': (
                                good.selections[0].model_copy(
                                    update={'selected_acknowledgment': 'collision'}
                                ),
                            ),
                        }
                    )
                    await ledger.stage_import(bad)
                    await ledger.seal_import(bad.id, IMPORT_MS)
                    tables = (
                        'evidence_events',
                        'event_targets',
                        'vote_receipts',
                        'receipt_state',
                        'claims',
                        'sessions',
                        'session_state',
                        'session_cases',
                        'coverage',
                        'coverage_members',
                        'import_sources',
                        'import_rows',
                        'import_execution',
                        'import_applications',
                        'family_state',
                    )
                    before = [
                        await transport.rows(f'SELECT * FROM {table} ORDER BY rowid')
                        for table in tables
                    ]

                    with pytest.raises(CommandRejected, match='^immutable$'):
                        await ledger.apply_import(
                            Context(
                                command_id='import', principal='admin', now=IMPORT_MS
                            ),
                            bad.id,
                        )

                    assert [
                        await transport.rows(f'SELECT * FROM {table} ORDER BY rowid')
                        for table in tables
                    ] == before
                    assert (
                        await transport.rows(
                            "SELECT * FROM sessions WHERE id = 'imported-session'"
                        )
                        == []
                    )

                    corrected = good.model_copy(update={'id': 'corrected'})
                    await ledger.stage_import(corrected)
                    await ledger.seal_import(corrected.id, IMPORT_MS + 1)
                    await ledger.apply_import(
                        Context(
                            command_id='import', principal='admin', now=IMPORT_MS + 1
                        ),
                        corrected.id,
                    )

                    assert await transport.rows(
                        "SELECT original_id FROM vote_receipts WHERE origin = 'legacy' "
                        'ORDER BY original_id'
                    ) == [{'original_id': '1'}, {'original_id': '2'}]
                    assert await transport.rows('SELECT count FROM coverage') == [
                        {'count': 2}
                    ]

            asyncio.run(scenario())

        def test_later_resolution_preserves_earlier_frozen_unresolved_exports(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await prepare(ledger, transport)
                    linked = await legacy_plan(ledger, transport)
                    raw = linked.model_copy(
                        update={
                            'observations': (),
                            'selections': (),
                            'dispositions': (
                                ImportDisposition(
                                    raw_seq=linked.observations[0].raw_seq,
                                    state='unresolved',
                                    reason='missing_proof',
                                ),
                                ImportDisposition(
                                    raw_seq=linked.observations[1].raw_seq,
                                    state='incompatible',
                                    reason='retired_output',
                                ),
                            ),
                        }
                    )
                    await ledger.stage_import(raw)
                    await ledger.seal_import(raw.id, IMPORT_MS)
                    finalized = await ledger.apply_import(
                        Context(
                            command_id='finalize', principal='admin', now=IMPORT_MS
                        ),
                        raw.id,
                    )
                    before = await ledger.freeze(
                        'before-link', 'writing', IMPORT_MS + 1
                    )
                    original = await export_rows(ledger, before.id, limit=1)
                    resolved = linked.model_copy(
                        update={
                            'id': 'resolve',
                            'revision': finalized.seq,
                            'initial': False,
                            'observations': linked.observations[:1],
                            'selections': (
                                linked.selections[0].model_copy(
                                    update={'selected_acknowledgment': 'legacy-ack:1'}
                                ),
                            ),
                            'dispositions': linked.dispositions[:1],
                        }
                    )
                    await ledger.stage_import(resolved)
                    await ledger.seal_import(resolved.id, IMPORT_MS + 2)

                    await ledger.apply_import(
                        Context(
                            command_id='resolve', principal='admin', now=IMPORT_MS + 3
                        ),
                        resolved.id,
                    )
                    after = await ledger.freeze('after-link', 'writing', IMPORT_MS + 4)
                    current = await export_rows(ledger, after.id, limit=2)

                    assert await export_rows(ledger, before.id, limit=3) == original
                    assert original['receipts'] == []
                    assert [row['original_id'] for row in current['receipts']] == ['1']
                    assert [row['seq'] for row in current['import_rows']] == [
                        row['seq'] for row in original['import_rows']
                    ]
                    assert current['import_sources'] == original['import_sources']
                    assert await transport.rows('SELECT count FROM coverage') == [
                        {'count': 1}
                    ]
                    assert all('raw' not in row for row in current['import_rows'])
                    assert 'synthetic-private-job' not in json.dumps(current)

            asyncio.run(scenario())

    class TestAnnotate:
        @pytest.mark.parametrize(
            ('mode', 'principal', 'rejection'),
            [
                ('local', 'other', 'annotation_target'),
                ('public', 'actor', 'private_material'),
            ],
            ids=['wrong-actor', 'public-deployment'],
        )
        def test_rejects_notes_without_local_receipt_authority(
            self, d1_runtime: D1Runtime, mode: Mode, principal: str, rejection: str
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await prepare(
                        ledger,
                        transport,
                        mode=mode,
                        quotas=tuple(
                            Quota(
                                operation=operation,
                                scope='actor',
                                window_ms=1_000,
                                limit=100,
                            )
                            for operation in ('issue', 'accept')
                        ),
                    )
                    lease = await ledger.issue(
                        Context(command_id='draw', principal='actor', now=10), issue()
                    )
                    receipt = await ledger.accept(
                        Context(command_id='vote', principal='actor', now=20),
                        Accept(
                            lease_id=lease.id,
                            acknowledgment='ack',
                            choice='a',
                            attribution=attribution(20) if mode == 'public' else (),
                        ),
                    )

                    with pytest.raises(CommandRejected, match=f'^{rejection}$'):
                        await ledger.annotate(
                            principal,
                            ReceiptAnnotation(kind='receipt', receipt_seq=receipt.seq),
                            'Synthetic private note.',
                        )

                    assert (
                        await transport.rows('SELECT * FROM private_annotations') == []
                    )
                    assert await transport.rows('SELECT count FROM coverage') == [
                        {'count': 1}
                    ]
                    assert (
                        await ledger.accept(
                            Context(command_id='vote', principal='actor', now=21),
                            Accept(lease_id=lease.id, acknowledgment='ack', choice='a'),
                        )
                        == receipt
                    )

            asyncio.run(scenario())

        @pytest.mark.parametrize(
            'redact_before_link',
            [True, False],
            ids=['raw-redaction', 'normalized-redaction'],
        )
        def test_import_note_redaction_survives_normalization_and_redelivery(
            self, d1_runtime: D1Runtime, redact_before_link: bool
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await prepare(ledger, transport)
                    plan = await legacy_plan(ledger, transport, receipts=1)
                    raw_target = ImportAnnotation(
                        kind='import', source_id='source', original_id='1'
                    )
                    await ledger.annotate(
                        'owner', raw_target, 'Synthetic private note.'
                    )

                    if redact_before_link:
                        await ledger.redact_annotation('owner', raw_target, IMPORT_MS)

                    await ledger.stage_import(plan)
                    await ledger.seal_import(plan.id, IMPORT_MS + 1)
                    await ledger.apply_import(
                        Context(
                            command_id='import', principal='admin', now=IMPORT_MS + 2
                        ),
                        plan.id,
                    )
                    receipts = await transport.rows(
                        "SELECT seq FROM vote_receipts WHERE origin = 'legacy'"
                    )
                    normalized = ReceiptAnnotation(
                        kind='receipt',
                        receipt_seq=TypeAdapter(int).validate_python(
                            receipts[0]['seq']
                        ),
                    )

                    if not redact_before_link:
                        await ledger.redact_annotation(
                            'owner', normalized, IMPORT_MS + 3
                        )

                    await ledger.annotate(
                        'owner', raw_target, 'Synthetic private note.'
                    )
                    await ledger.annotate(
                        'owner', normalized, 'Another delivered note.'
                    )
                    vote = legacy_vote(1)
                    await ledger.preserve_import(
                        'source',
                        ImportRow(
                            kind='receipt',
                            original_id='1',
                            source_hash=digest(vote),
                            raw=vote,
                        ),
                    )
                    frozen = await ledger.freeze('redacted', 'writing', IMPORT_MS + 4)
                    rows = await export_rows(ledger, frozen.id, limit=2)

                    assert await transport.rows(
                        'SELECT receipt_seq, source_id, original_id, note, redacted_at '
                        'FROM private_annotations'
                    ) == [
                        {
                            'receipt_seq': None,
                            'source_id': 'source',
                            'original_id': '1',
                            'note': None,
                            'redacted_at': IMPORT_MS
                            if redact_before_link
                            else IMPORT_MS + 3,
                        }
                    ]
                    assert 'Synthetic private note.' not in json.dumps(rows)
                    assert 'Another delivered note.' not in json.dumps(rows)
                    assert 'Synthetic private note.' not in json.dumps(
                        await transport.rows('SELECT raw FROM import_rows')
                    )
                    assert not any('annotations' in section for section in rows)

            asyncio.run(scenario())

    class TestFreeze:
        def test_frozen_pages_preserve_raw_cutoff_and_inventory_after_later_changes(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await prepare(ledger, transport)
                    await ledger.register_actor(
                        Actor(id='owner', created_at=2, owner_namespace='legacy-owner')
                    )
                    source = ImportSource(
                        id='source',
                        family='writing',
                        owner_id='owner',
                        source_schema='2',
                        file_digest=digest('synthetic-source'),
                        sessions=1,
                        receipts=2,
                        withdrawals=0,
                    )
                    await ledger.register_import(source)
                    session = LegacySession(
                        id='legacy-session',
                        family='writing',
                        started_at='2026-01-01T00:00:00+00:00',
                    )
                    session_row = ImportRow(
                        kind='session',
                        original_id=session.id,
                        source_hash=digest(session),
                        raw=session,
                    )
                    vote = legacy_vote(1)
                    first = ImportRow(
                        kind='receipt',
                        original_id='1',
                        source_hash=digest(vote),
                        raw=vote,
                    )
                    await ledger.preserve_import(source.id, session_row)
                    await ledger.preserve_import(source.id, first)
                    frozen = await ledger.freeze('frozen', 'writing', 20)
                    before = await export_rows(ledger, frozen.id, limit=1)

                    await ledger.preserve_import(source.id, first)
                    later = legacy_vote(2)
                    await ledger.preserve_import(
                        source.id,
                        ImportRow(
                            kind='receipt',
                            original_id='2',
                            source_hash=digest(later),
                            raw=later,
                        ),
                    )
                    await publish(ledger, suffix='next', cases=4)
                    await ledger.activate(
                        Context(command_id='activate:next', principal='admin', now=30),
                        'writing',
                        frozen.family_revision,
                        'evaluation:next',
                        'publication:next',
                        'points:next',
                    )
                    after = await export_rows(ledger, frozen.id, limit=3)
                    current = await ledger.freeze('current', 'writing', 40)
                    current_rows = await export_rows(ledger, current.id, limit=2)

                    assert after == before
                    assert await ledger.freeze(frozen.id, 'writing', 50) == frozen
                    assert (
                        frozen.namespace == 'test-deployment'
                        and frozen.schema_version == 1
                    )
                    assert {
                        (row['row_kind'], row['original_id'])
                        for row in before['import_rows']
                    } == {
                        ('session', 'legacy-session'),
                        ('receipt', '1'),
                    }
                    assert {
                        (row['row_kind'], row['original_id'])
                        for row in current_rows['import_rows']
                    } == {
                        ('session', 'legacy-session'),
                        ('receipt', '1'),
                        ('receipt', '2'),
                    }
                    assert all(
                        row['initial_disposition'] == 'unresolved'
                        for row in current_rows['import_rows']
                    )
                    assert before['receipts'] == current_rows['receipts'] == []
                    assert 'synthetic-private-job' not in json.dumps(current_rows)
                    assert all('raw' not in row for row in current_rows['import_rows'])
                    assert (
                        len(before['outputs']) == 6
                        and len(current_rows['outputs']) == 8
                    )

            asyncio.run(scenario())

    class TestModerate:
        def test_applies_later_chunks_for_the_same_reviewed_historical_claim(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await prepare(ledger, transport)
                    receipts = []
                    version = 0

                    for index in range(251):
                        now = 100 + index * 10
                        lease = await ledger.issue(
                            Context(
                                command_id=f'issue:{index}', principal='actor', now=now
                            ),
                            issue(lease=f'lease:{index}').model_copy(
                                update={
                                    'session_version': version,
                                    'claim_version': version,
                                }
                            ),
                        )
                        receipt = await ledger.accept(
                            Context(
                                command_id=f'accept:{index}',
                                principal='actor',
                                now=now + 1,
                            ),
                            Accept(
                                lease_id=lease.id,
                                acknowledgment=f'ack:{index}',
                                choice='a',
                            ),
                        )
                        receipts.append(receipt)
                        version += 2

                        if index != 250:
                            await ledger.withdraw(
                                Context(
                                    command_id=f'withdraw:{index}',
                                    principal='actor',
                                    now=now + 2,
                                ),
                                'session',
                                version,
                                receipt.seq,
                            )
                            version += 1

                    await ledger.stage_moderation(
                        Moderation(
                            id='bulk',
                            family='writing',
                            principal='admin',
                            action='exclude',
                            reason='Reviewed synthetic receipt history.',
                            created_at=3_000,
                            targets=tuple(
                                Target(receipt_seq=value.seq, claim_version=version)
                                for value in receipts
                            ),
                        )
                    )
                    await ledger.seal_moderation('bulk', 3_000)

                    first = await ledger.moderate(
                        Context(command_id='moderate:0', principal='admin', now=3_010),
                        'bulk',
                        0,
                    )
                    assert await transport.rows('SELECT count FROM coverage') == [
                        {'count': 1}
                    ]
                    second = await ledger.moderate(
                        Context(command_id='moderate:1', principal='admin', now=3_020),
                        'bulk',
                        1,
                    )

                    assert await transport.rows(
                        'SELECT excluded, count(*) AS receipts FROM receipt_state '
                        'GROUP BY excluded ORDER BY excluded'
                    ) == [
                        {'excluded': first.seq, 'receipts': 250},
                        {'excluded': second.seq, 'receipts': 1},
                    ]
                    assert await transport.rows('SELECT count FROM coverage') == [
                        {'count': 0}
                    ]
                    assert (
                        await ledger.moderate(
                            Context(
                                command_id='moderate:1', principal='admin', now=3_030
                            ),
                            'bulk',
                            1,
                        )
                        == second
                    )

            asyncio.run(scenario())

        @pytest.mark.parametrize(
            'sql',
            [
                "UPDATE moderation_batches SET reason = 'changed' WHERE id = 'sealed'",
                "DELETE FROM moderation_batches WHERE id = 'sealed'",
                """
                INSERT OR REPLACE INTO moderation_batches
                SELECT id, family, principal, action, 'changed', target_count,
                    target_digest, created_at, sealed_at
                FROM moderation_batches WHERE id = 'sealed'
                """,
                """
                INSERT OR REPLACE INTO moderation_applications
                SELECT batch_id, chunk_index, 1 FROM moderation_applications
                """,
                """
                INSERT OR REPLACE INTO moderation_applications
                SELECT batch_id, 1, event_seq FROM moderation_applications
                """,
                """
                INSERT OR REPLACE INTO moderation_targets
                SELECT batch_id, 1, receipt_seq, claim_version, exclusion_seq
                FROM moderation_targets WHERE batch_id = 'unsealed'
                """,
            ],
            ids=[
                'header-update',
                'header-delete',
                'header-replace',
                'application-pk',
                'application-event',
                'target-receipt',
            ],
        )
        def test_preserves_all_reviewed_moderation_identities(
            self, d1_runtime: D1Runtime, sql: str
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await prepare(ledger, transport)
                    lease = await ledger.issue(
                        Context(command_id='issue', principal='actor', now=10), issue()
                    )
                    receipt = await ledger.accept(
                        Context(command_id='accept', principal='actor', now=20),
                        Accept(lease_id=lease.id, acknowledgment='ack', choice='a'),
                    )

                    for name in ('sealed', 'unsealed'):
                        await ledger.stage_moderation(
                            Moderation(
                                id=name,
                                family='writing',
                                principal='admin',
                                action='exclude',
                                reason='Synthetic reviewed target.',
                                created_at=30,
                                targets=(
                                    Target(receipt_seq=receipt.seq, claim_version=2),
                                ),
                            )
                        )

                    await ledger.seal_moderation('sealed', 30)
                    event = await ledger.moderate(
                        Context(command_id='moderate', principal='admin', now=40),
                        'sealed',
                        0,
                    )
                    tables = (
                        'moderation_batches',
                        'moderation_targets',
                        'moderation_applications',
                    )
                    before = [
                        await transport.rows(f'SELECT * FROM {table} ORDER BY 1, 2')
                        for table in tables
                    ]

                    with pytest.raises(CommandRejected, match='^immutable$'):
                        await transport.execute(Statement(sql=sql))

                    assert [
                        await transport.rows(f'SELECT * FROM {table} ORDER BY 1, 2')
                        for table in tables
                    ] == before
                    assert await transport.rows(
                        'SELECT excluded FROM receipt_state'
                    ) == [{'excluded': event.seq}]
                    assert await transport.rows('SELECT count FROM coverage') == [
                        {'count': 0}
                    ]

            asyncio.run(scenario())
