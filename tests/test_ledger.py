import asyncio
import json
from collections.abc import Iterator
from textwrap import dedent
from typing import get_args

import pytest
from pydantic import TypeAdapter, ValidationError

from assistant_bench_arena.investigation import (
    InvestigatedReceipt,
    InvestigationPage,
    NetworkLookup,
    ReceiptFilter,
)
from assistant_bench_arena.ledger import Ledger
from assistant_bench_arena.ledger_models import (
    Accept,
    Actor,
    Admission,
    Attribution,
    BootstrapConfiguration,
    Context,
    Export,
    ExportSection,
    Issue,
    Lease,
    Moderation,
    Network,
    NetworkTag,
    Publication,
    Quota,
    Receipt,
    RenderedOutput,
    Row,
    Session,
    Statement,
    Target,
    VoteMeaning,
)
from assistant_bench_arena.releases import Release, evaluation_identity, settlements
from assistant_bench_arena.schema import SCHEMA_ID, bootstrap_statements, parse_schema
from assistant_bench_arena.serialization import digest
from assistant_bench_arena.snapshots import OutputContentApproval
from assistant_bench_arena.transport import CommandRejected
from ledger_fixtures import case_key, make_release, output_key
from ledger_support import (
    D1Runtime,
    D1Transport,
    publish_presentation,
    publish_release,
    schema_statements,
    seed_actor,
)


RETENTION_MS = 60 * 86_400_000
QUOTAS = tuple(
    Quota(operation=operation, scope='actor', window_ms=1_000, limit=10_000)
    for operation in ('admit', 'renew', 'issue', 'accept')
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
    failing_case: int | None = None,
    seal: bool = True,
) -> Release:
    release = make_release(
        family=family, suffix=suffix, cases=cases, failing_case=failing_case
    )
    await publish_release(ledger, release, seal=seal)
    return release


async def prepare(
    ledger: Ledger,
    transport: D1Transport,
    *,
    quotas: tuple[Quota, ...] = QUOTAS,
) -> Release:
    await transport.reset()
    await transport.bootstrap(
        BootstrapConfiguration(
            namespace='test-deployment',
            quotas=quotas,
            attribution_ms=RETENTION_MS,
        )
    )
    release = await publish(ledger)
    await ledger.activate(
        Context(command_id='activate:1', principal='admin', now=1),
        'writing',
        0,
        release.evaluation.id,
        release.publication.id,
        release.points.id,
    )
    await seed_actor(transport, Actor(id='actor', created_at=2))
    await ledger.start_session(
        Session(id='session', actor_id='actor', family='writing', created_at=2)
    )
    return release


def issue(*, session: str = 'session', lease: str = 'lease', case: int = 0) -> Issue:
    return Issue(
        lease_id=lease,
        session_id=session,
        session_version=0,
        claim_id=f'claim:{session}:{case}',
        claim_version=0,
        case_key=case_key(case),
        a_output=output_key(case, 'large'),
        b_output=output_key(case, 'small'),
        expires_at=10_000,
    )


class TestParseSchema:
    @pytest.mark.parametrize('source', ['', ' \n\t '], ids=['empty', 'whitespace'])
    def test_empty_input_has_no_statements(self, source: str) -> None:
        assert parse_schema(source) == ()

    @pytest.mark.parametrize(
        'source',
        ['CREATE TABLE unfinished(value)', 'SELECT 1; SELECT 2', '-- Comment tail.'],
        ids=['missing-terminator', 'partial-tail', 'comment-tail'],
    )
    def test_nonempty_unterminated_tail_is_rejected(self, source: str) -> None:
        with pytest.raises(ValueError, match='unterminated SQL statement'):
            parse_schema(source)

    def test_trigger_body_comments_and_quoted_semicolons_execute_together(
        self, d1_runtime: D1Runtime
    ) -> None:
        source = dedent("""
            -- A prefix comment containing a semicolon ;
            CREATE TABLE samples(id INTEGER PRIMARY KEY, value TEXT);
            CREATE TABLE audit(sample_id INTEGER, phase TEXT, value TEXT);
            CREATE TRIGGER record_sample AFTER INSERT ON samples BEGIN
                /* A body comment containing a semicolon ; */
                INSERT INTO audit VALUES (NEW.id, 'before;', NEW.value);
                UPDATE samples SET value = value || '; changed' WHERE id = NEW.id;
                INSERT INTO audit SELECT id, 'after;', value FROM samples
                    WHERE id = NEW.id;
            END;
        """)

        async def scenario() -> None:
            async with d1_runtime.client() as (_, transport):
                await transport.reset()
                await transport.request(list(parse_schema(source)), batch=True)

                await transport.execute(
                    Statement(
                        sql='INSERT INTO samples(id, value) VALUES (?, ?)',
                        params=(7, 'original; value'),
                    )
                )

                assert await transport.rows('SELECT * FROM samples') == [
                    {'id': 7, 'value': 'original; value; changed'}
                ]
                assert await transport.rows('SELECT * FROM audit ORDER BY rowid') == [
                    {'sample_id': 7, 'phase': 'before;', 'value': 'original; value'},
                    {
                        'sample_id': 7,
                        'phase': 'after;',
                        'value': 'original; value; changed',
                    },
                ]

        asyncio.run(scenario())


class TestLedger:
    class TestBootstrap:
        def test_atomic_profile_installs_exact_quotas_and_immutable_identity(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await transport.reset()
                    assert await ledger.status() is None
                    config = BootstrapConfiguration(
                        namespace='deployment',
                        quotas=(
                            Quota(
                                operation='issue',
                                scope='deployment',
                                window_ms=2_000,
                                limit=7,
                            ),
                            Quota(
                                operation='accept',
                                scope='actor',
                                window_ms=1_000,
                                limit=10,
                            ),
                        ),
                        attribution_ms=RETENTION_MS,
                    )

                    await transport.bootstrap(config)

                    status = await ledger.status()
                    assert status is not None
                    assert status.model_dump() == config.model_dump() | {
                        'schema_id': SCHEMA_ID
                    }
                    assert await transport.rows(
                        'SELECT operation, scope, window_ms, allowance '
                        'FROM quota_policies ORDER BY operation'
                    ) == [
                        {
                            'operation': 'accept',
                            'scope': 'actor',
                            'window_ms': 1_000,
                            'allowance': 10,
                        },
                        {
                            'operation': 'issue',
                            'scope': 'deployment',
                            'window_ms': 2_000,
                            'allowance': 7,
                        },
                    ]
                    original = await transport.rows('SELECT * FROM deployment')

                    for sql in (
                        "UPDATE deployment SET namespace = 'different'",
                        'UPDATE deployment SET attribution_ms = attribution_ms - 1',
                        'DELETE FROM deployment',
                        'INSERT OR REPLACE INTO deployment SELECT * FROM deployment',
                    ):
                        with pytest.raises(CommandRejected, match='^immutable$'):
                            await transport.execute(Statement(sql=sql))

                    assert await transport.rows('SELECT * FROM deployment') == original
                    assert await ledger.status() == status

            asyncio.run(scenario())

        @pytest.mark.parametrize(
            ('lifetime', 'duplicate'),
            [
                (None, False),
                (0, False),
                (RETENTION_MS + 1, False),
                (RETENTION_MS, True),
            ],
            ids=[
                'missing-lifetime',
                'zero-lifetime',
                'over-60-days',
                'quota',
            ],
        )
        def test_invalid_profile_leaves_the_database_empty(
            self,
            d1_runtime: D1Runtime,
            lifetime: int | None,
            duplicate: bool,
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await transport.reset()
                    quota = Quota(
                        operation='accept', scope='actor', window_ms=1_000, limit=10
                    )

                    with pytest.raises(ValidationError):
                        await transport.bootstrap(
                            BootstrapConfiguration.model_validate(
                                {
                                    'namespace': 'deployment',
                                    'quotas': (quota, quota) if duplicate else (),
                                    'attribution_ms': lifetime,
                                }
                            )
                        )

                    assert await ledger.status() is None

            asyncio.run(scenario())

        @pytest.mark.parametrize('failure', ['marker', 'late-command'])
        def test_late_bootstrap_failure_rolls_back_schema_profile_and_quotas(
            self, d1_runtime: D1Runtime, failure: str
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await transport.reset()
                    config = BootstrapConfiguration(
                        namespace='deployment',
                        quotas=(
                            Quota(
                                operation='accept',
                                scope='actor',
                                window_ms=1_000,
                                limit=10,
                            ),
                        ),
                        attribution_ms=RETENTION_MS,
                    )
                    marker = bootstrap_statements(config)[0]
                    statements = schema_statements()

                    if failure == 'marker':
                        statements.append(
                            marker.model_copy(
                                update={'params': (*marker.params[:-1], 0)}
                            )
                        )
                    else:
                        statements.extend(
                            (
                                marker,
                                Statement(
                                    sql='INSERT INTO actors(id, created_at) '
                                    "VALUES ('late-invalid', -1)"
                                ),
                            )
                        )

                    with pytest.raises(RuntimeError, match='CHECK constraint failed'):
                        await transport.request(statements, batch=True)

                    assert await ledger.status() is None

            asyncio.run(scenario())

        @pytest.mark.parametrize(
            ('table', 'sql'),
            [
                ('actors', "INSERT INTO actors(id, created_at) VALUES ('actor', 1)"),
                ('policies', "INSERT INTO policies(id, body) VALUES ('policy', '{}')"),
                (
                    'evidence_events',
                    'INSERT INTO evidence_events(command_id, kind, family, principal, '
                    "request_hash, created_at, data) VALUES ('command', 'activate', "
                    "'writing', 'admin', 'hash', 1, '{}')",
                ),
                (
                    'moderation_batches',
                    'INSERT INTO moderation_batches(id, family, principal, action, '
                    'reason, target_count, target_digest, created_at) '
                    "VALUES ('batch', 'writing', 'admin', 'exclude', 'Review.', "
                    "1, 'hash', 1)",
                ),
            ],
            ids=['actor', 'policy', 'event', 'moderation'],
        )
        def test_domain_roots_reject_an_unmarked_schema(
            self, d1_runtime: D1Runtime, table: str, sql: str
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await transport.reset()
                    await transport.request(schema_statements(), batch=True)

                    with pytest.raises(
                        RuntimeError, match='FOREIGN KEY constraint failed'
                    ):
                        await transport.execute(Statement(sql=sql))

                    with pytest.raises(
                        ValueError, match='no unique initialized profile'
                    ):
                        await ledger.status()

                    assert await transport.rows(f'SELECT * FROM {table}') == []
                    assert await transport.rows('SELECT * FROM actor_expiry') == []

            asyncio.run(scenario())

    class TestSeal:
        @pytest.mark.parametrize(
            ('kind', 'table'),
            [
                ('evaluation', 'evaluations'),
                ('publication', 'publications'),
                ('moderation', 'moderation_batches'),
            ],
        )
        def test_applied_fact_seals_replay_without_replacing_the_first_timestamp(
            self, d1_runtime: D1Runtime, kind: str, table: str
        ) -> None:
            async def scenario() -> None:
                async with (
                    d1_runtime.client() as (ledger, transport),
                    d1_runtime.client() as (other, _),
                ):
                    release = await prepare(ledger, transport)
                    fact_id = 'fact'
                    now = 20

                    if kind in ('evaluation', 'publication'):
                        release = await publish(ledger, suffix='2', cases=4, seal=False)
                        fact_id = release.evaluation.id

                        if kind == 'publication':
                            await ledger.seal_evaluation(release.evaluation.id, 10)
                            await ledger.stage_publication(release.publication)

                            for rendered in release.rendered:
                                await ledger.stage_rendered(
                                    release.publication.id, rendered
                                )

                            fact_id = release.publication.id
                    elif kind == 'moderation':
                        lease = await ledger.issue(
                            Context(command_id='draw', principal='actor', now=5),
                            issue(),
                        )
                        receipt = await ledger.accept(
                            Context(command_id='vote', principal='actor', now=6),
                            Accept(
                                lease_id=lease.id,
                                acknowledgment='ack',
                                choice='a',
                                attribution=attribution(6),
                            ),
                        )
                        await ledger.stage_moderation(
                            Moderation(
                                id=fact_id,
                                family='writing',
                                principal='admin',
                                action='exclude',
                                reason='Reviewed synthetic exclusion.',
                                created_at=10,
                                targets=(
                                    Target(receipt_seq=receipt.seq, claim_version=2),
                                ),
                            )
                        )
                    seals = {
                        'evaluation': ledger.seal_evaluation,
                        'publication': ledger.seal_publication,
                        'moderation': ledger.seal_moderation,
                    }
                    other_seals = {
                        'evaluation': other.seal_evaluation,
                        'publication': other.seal_publication,
                        'moderation': other.seal_moderation,
                    }

                    await seals[kind](fact_id, now)

                    assert await transport.rows(
                        f'SELECT sealed_at FROM {table} WHERE id = ?', fact_id
                    ) == [{'sealed_at': now}]
                    context = Context(command_id='apply', principal='admin', now=now)

                    if kind in ('evaluation', 'publication'):
                        if kind == 'evaluation':
                            await publish_presentation(ledger, release)
                        else:
                            await ledger.stage_points(release.points, now)

                        await ledger.activate(
                            context,
                            'writing',
                            1,
                            release.evaluation.id,
                            release.publication.id,
                            release.points.id,
                        )
                        state = await ledger.read.family('writing')
                        assert state is not None
                        assert state.evaluation_id == release.evaluation.id
                    elif kind == 'moderation':
                        await ledger.moderate(context, fact_id, 0)
                        assert (
                            await transport.rows('SELECT * FROM eligible_claims') == []
                        )
                    facts = await transport.rows(f'SELECT * FROM {table} ORDER BY id')
                    events = await transport.rows('SELECT * FROM evidence_events')

                    await seals[kind](fact_id, now + 1)
                    results = await asyncio.gather(
                        seals[kind](fact_id, now + 2),
                        other_seals[kind](fact_id, now + 3),
                    )

                    assert all(result is None for result in results)
                    assert (
                        await transport.rows(f'SELECT * FROM {table} ORDER BY id')
                        == facts
                    )
                    assert (
                        await transport.rows('SELECT * FROM evidence_events') == events
                    )

                    with pytest.raises((CommandRejected, ValueError)):
                        await seals[kind]('missing-fact', now + 4)

                    assert (
                        await transport.rows(f'SELECT * FROM {table} ORDER BY id')
                        == facts
                    )

            asyncio.run(scenario())

    class TestSealEvaluation:
        @pytest.mark.parametrize('failure', ['absent', 'errored'])
        def test_authoritative_seal_rejects_a_passing_unusable_output(
            self, d1_runtime: D1Runtime, failure: str
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await transport.reset()
                    await transport.bootstrap(
                        BootstrapConfiguration(
                            namespace='deployment',
                            quotas=(),
                            attribution_ms=RETENTION_MS,
                        )
                    )
                    release = make_release(cases=1)
                    original = release.outputs[0]
                    content_digest = (
                        digest(None) if failure == 'absent' else original.content_digest
                    )
                    key = digest(
                        {
                            'source_digest': original.metadata.source_digest,
                            'content_digest': content_digest,
                        }
                    )
                    changed = original.model_copy(
                        update={
                            'key': key,
                            'content_digest': content_digest,
                            'metadata': original.metadata.model_copy(
                                update={
                                    'errored': failure == 'errored',
                                    'approval': OutputContentApproval(
                                        output_key=key,
                                        content_digest=content_digest,
                                    ),
                                }
                            ),
                        }
                    )
                    outputs = (changed, *release.outputs[1:])
                    numerics = settlements(outputs)
                    evaluation = release.evaluation.model_copy(
                        update={
                            'id': evaluation_identity(
                                release.evaluation.manifest,
                                release.cases,
                                release.systems,
                                outputs,
                                numerics,
                            ),
                            'numerics': len(numerics),
                        }
                    )
                    await ledger.stage_policy(evaluation.policy_id, release.policy)
                    await ledger.stage_evaluation(evaluation)

                    for system in release.systems:
                        await ledger.stage_system(evaluation.id, system)

                    for case in release.cases:
                        await ledger.stage_case(evaluation.id, case)

                    for output in outputs:
                        await ledger.stage_output(evaluation.id, output)

                    for row in numerics:
                        await ledger.stage_numerical(evaluation.id, row)

                    with pytest.raises(
                        ValueError,
                        match='An absent or errored output cannot pass its gate',
                    ):
                        await ledger.seal_evaluation(evaluation.id, 1)

                    assert await transport.rows(
                        'SELECT sealed_at FROM evaluations'
                    ) == [{'sealed_at': None}]
                    assert await transport.rows('SELECT * FROM family_state') == []

            asyncio.run(scenario())

        @pytest.mark.parametrize('target', ['case', 'output', 'policy'])
        def test_public_seal_rejects_unknown_persisted_metadata(
            self, d1_runtime: D1Runtime, target: str
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await transport.reset()
                    await transport.bootstrap(
                        BootstrapConfiguration(
                            namespace='public',
                            quotas=(),
                            attribution_ms=RETENTION_MS,
                        )
                    )
                    release = make_release()
                    evaluation = release.evaluation

                    if target == 'policy':
                        await transport.execute(
                            Statement(
                                sql="""
                                INSERT INTO policies(id, body) VALUES (?,
                                    json_set(?, '$.private_endpoint', ?))
                            """,
                                params=(
                                    evaluation.policy_id,
                                    release.policy.model_dump_json(),
                                    'Synthetic private marker.',
                                ),
                            )
                        )
                    else:
                        await ledger.stage_policy(evaluation.policy_id, release.policy)

                    await ledger.stage_evaluation(evaluation)

                    for system in release.systems:
                        await ledger.stage_system(evaluation.id, system)

                    for case in release.cases:
                        if target == 'case':
                            await transport.execute(
                                Statement(
                                    sql="""
                                    INSERT INTO evaluation_cases(evaluation_id,
                                        case_key, category, source, instruction_digest,
                                        body) VALUES (?, ?, ?, ?, ?, json_set(?,
                                        '$.metadata.private_endpoint', ?))
                                """,
                                    params=(
                                        evaluation.id,
                                        case.key,
                                        case.category,
                                        case.source,
                                        case.instruction_digest,
                                        case.model_dump_json(),
                                        'Synthetic private marker.',
                                    ),
                                )
                            )
                        else:
                            await ledger.stage_case(evaluation.id, case)

                    for output in release.outputs:
                        if target == 'output':
                            await transport.execute(
                                Statement(
                                    sql="""
                                    INSERT INTO evaluation_outputs(evaluation_id,
                                        output_key, case_key, system_id, content_digest,
                                        verdict, output_length, approved_public, body)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, json_set(?,
                                        '$.metadata.private_endpoint', ?))
                                """,
                                    params=(
                                        evaluation.id,
                                        output.key,
                                        output.case_key,
                                        output.system_id,
                                        output.content_digest,
                                        output.verdict,
                                        output.length,
                                        int(output.approved_public),
                                        output.model_dump_json(),
                                        'Synthetic private marker.',
                                    ),
                                )
                            )
                        else:
                            await ledger.stage_output(evaluation.id, output)

                    with pytest.raises(ValidationError, match='extra_forbidden'):
                        await ledger.seal_evaluation(evaluation.id, 1)

                    assert await transport.rows(
                        'SELECT sealed_at FROM evaluations'
                    ) == [{'sealed_at': None}]
                    assert await transport.rows('SELECT * FROM family_state') == []

            asyncio.run(scenario())

    class TestSealPublication:
        @pytest.mark.parametrize(
            'mutation', ['missing-approval', 'unknown-presentation', 'changed-render']
        )
        def test_public_seal_rejects_changed_or_unapproved_presentation(
            self, d1_runtime: D1Runtime, mutation: str
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await transport.reset()
                    await transport.bootstrap(
                        BootstrapConfiguration(
                            namespace='public',
                            quotas=(),
                            attribution_ms=RETENTION_MS,
                        )
                    )
                    release = await publish(ledger, seal=False)
                    await ledger.seal_evaluation(release.evaluation.id, 0)
                    publication = release.publication

                    if mutation == 'changed-render':
                        await ledger.stage_publication(publication)
                    else:
                        path, value = (
                            ('$.approval', None)
                            if mutation == 'missing-approval'
                            else ('$.presentation.private_endpoint', 'private')
                        )
                        await transport.execute(
                            Statement(
                                sql="""
                                INSERT INTO publications(id, evaluation_id, body,
                                    output_count) VALUES (?, ?, json_set(?, ?, ?), ?)
                            """,
                                params=(
                                    publication.id,
                                    publication.evaluation_id,
                                    publication.model_dump_json(),
                                    path,
                                    value,
                                    publication.outputs,
                                ),
                            )
                        )

                    for index, rendered in enumerate(release.rendered):
                        if mutation == 'changed-render' and index == 0:
                            rendered = RenderedOutput(
                                output_key=rendered.output_key,
                                render=rendered.render,
                                content='Unapproved replacement.',
                                content_digest=rendered.content_digest,
                                approved_public=rendered.approved_public,
                            )

                        await ledger.stage_rendered(publication.id, rendered)

                    with pytest.raises(ValueError):
                        await ledger.seal_publication(publication.id, 1)

                    assert await transport.rows(
                        'SELECT sealed_at FROM publications'
                    ) == [{'sealed_at': None}]
                    assert await transport.rows('SELECT * FROM family_state') == []

            asyncio.run(scenario())

    class TestOpenSession:
        def test_concurrent_replay_preserves_first_clock_and_exact_owner_family(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with (
                    d1_runtime.client() as (ledger, transport),
                    d1_runtime.client() as (other, _),
                ):
                    await prepare(ledger, transport)
                    await seed_actor(transport, Actor(id='other', created_at=2))
                    session_id = 'opaque-session'

                    results = await asyncio.gather(
                        ledger.open_session('actor', session_id, 'writing', 20),
                        other.open_session('actor', session_id, 'writing', 30),
                    )

                    assert results[0] == results[1]
                    assert results[0].id == session_id
                    assert results[0].eligible_votes == 0
                    assert results[0].undo_acknowledgment is None
                    original = await transport.rows(
                        'SELECT actor_id, family, created_at '
                        'FROM sessions WHERE id = ?',
                        session_id,
                    )
                    assert original == [
                        {
                            'actor_id': 'actor',
                            'family': 'writing',
                            'created_at': original[0]['created_at'],
                        }
                    ]
                    assert original[0]['created_at'] in (20, 30)
                    assert (
                        await ledger.open_session('actor', session_id, 'writing', 40)
                        == results[0]
                    )

                    with pytest.raises(CommandRejected, match='^conflict$'):
                        await ledger.open_session('other', session_id, 'writing', 40)

                    with pytest.raises(CommandRejected, match='^conflict$'):
                        await ledger.open_session(
                            'actor', session_id, 'other-family', 40
                        )

                    assert (
                        await transport.rows(
                            'SELECT actor_id, family, created_at '
                            'FROM sessions WHERE id = ?',
                            session_id,
                        )
                        == original
                    )

            asyncio.run(scenario())

    class TestRead:
        def test_session_progress_and_family_status_track_undo_and_moderation(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await prepare(ledger, transport)
                    empty = await ledger.read.session('actor', 'session')
                    initial = await ledger.investigation.status('writing')
                    assert empty is not None and initial is not None
                    assert empty.eligible_votes == initial.eligible_votes == 0
                    assert empty.undo_acknowledgment is None
                    assert initial.sampling_source is None
                    assert initial.receipt_cutoff == 0
                    assert await ledger.investigation.status('unknown') is None
                    assert await ledger.read.session('other', 'session') is None

                    receipts: list[Receipt] = []

                    for index in range(2):
                        lease = await ledger.issue(
                            Context(
                                command_id=f'draw:{index}',
                                principal='actor',
                                now=10 + index * 10,
                            ),
                            issue(lease=f'lease:{index}', case=index).model_copy(
                                update={'session_version': index * 2}
                            ),
                        )
                        receipts.append(
                            await ledger.accept(
                                Context(
                                    command_id=f'vote:{index}',
                                    principal='actor',
                                    now=11 + index * 10,
                                ),
                                Accept(
                                    lease_id=lease.id,
                                    acknowledgment=f'ack:{index}',
                                    choice='a',
                                    attribution=attribution(11 + index * 10),
                                ),
                            )
                        )

                    voted = await ledger.read.session('actor', 'session')
                    assert voted is not None
                    assert voted.eligible_votes == 2
                    assert voted.undo_acknowledgment == receipts[-1].acknowledgment
                    withdrawn = await ledger.withdraw(
                        Context(command_id='undo-latest', principal='actor', now=30),
                        'session',
                        voted.version,
                        receipts[-1].seq,
                    )
                    remaining = await ledger.read.session('actor', 'session')
                    status = await ledger.investigation.status('writing')
                    assert remaining is not None and status is not None
                    assert remaining.eligible_votes == status.eligible_votes == 1
                    assert remaining.undo_acknowledgment == receipts[0].acknowledgment
                    assert status.event_cutoff == withdrawn.seq > status.receipt_cutoff
                    assert status.receipt_cutoff == receipts[-1].seq
                    assert status.state.last_evidence_seq == withdrawn.seq

                    await ledger.stage_moderation(
                        Moderation(
                            id='exclude',
                            family='writing',
                            principal='admin',
                            action='exclude',
                            reason='Reviewed synthetic exclusion.',
                            created_at=40,
                            targets=(
                                Target(receipt_seq=receipts[0].seq, claim_version=2),
                            ),
                        )
                    )
                    await ledger.seal_moderation('exclude', 40)
                    excluded = await ledger.moderate(
                        Context(command_id='exclude', principal='admin', now=40),
                        'exclude',
                        0,
                    )
                    moderated = await ledger.read.session('actor', 'session')
                    status = await ledger.investigation.status('writing')
                    assert moderated is not None and status is not None
                    assert moderated.eligible_votes == status.eligible_votes == 0
                    assert moderated.undo_acknowledgment == receipts[0].acknowledgment
                    assert status.event_cutoff == excluded.seq
                    assert status.receipt_cutoff == receipts[-1].seq
                    await ledger.withdraw(
                        Context(command_id='undo-excluded', principal='actor', now=50),
                        'session',
                        moderated.version,
                        receipts[0].seq,
                    )

                    finished = await ledger.read.session('actor', 'session')
                    assert finished is not None
                    assert finished.eligible_votes == 0
                    assert finished.undo_acknowledgment is None

            asyncio.run(scenario())

        def test_large_release_round_trips_exactly_across_bounded_native_pages(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await transport.reset()
                    await transport.bootstrap(
                        BootstrapConfiguration(
                            namespace='large-release',
                            quotas=(),
                            attribution_ms=RETENTION_MS,
                        )
                    )
                    release = await publish(ledger, cases=60)

                    evaluation = await ledger.read.evaluation(release.evaluation.id)
                    publication, rendered = await ledger.read.publication(
                        release.publication.id
                    )
                    assert (
                        await ledger.read.publication_metadata(release.publication.id)
                        == publication
                    )

                    assert evaluation.evaluation == release.evaluation
                    assert evaluation.policy == release.policy
                    assert {row.key: row for row in evaluation.cases} == {
                        row.key: row for row in release.cases
                    }
                    assert {row.key: row for row in evaluation.outputs} == {
                        row.key: row for row in release.outputs
                    }
                    assert evaluation.systems == release.systems
                    assert publication == release.publication
                    assert {row.output_key: row for row in rendered} == {
                        row.output_key: row for row in release.rendered
                    }
                    assert await ledger.read.points(release.points.id) == release.points

            asyncio.run(scenario())

    class TestInvestigation:
        @pytest.mark.parametrize(
            'wide', [False, True], ids=['row-limit', 'utf8-byte-limit']
        )
        def test_pages_include_every_original_receipt_with_current_claim_state(
            self, d1_runtime: D1Runtime, wide: bool
        ) -> None:
            def identifier(prefix: str, length: int = 256) -> str:
                return (
                    prefix + chr(0x10000) * (length - len(prefix)) if wide else prefix
                )

            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await transport.reset()
                    await transport.bootstrap(
                        BootstrapConfiguration(
                            namespace='deployment',
                            quotas=tuple(
                                Quota(
                                    operation=operation,
                                    scope='actor',
                                    window_ms=86400000,
                                    limit=1000,
                                )
                                for operation in ('issue', 'accept')
                            ),
                            attribution_ms=RETENTION_MS,
                        )
                    )
                    release = make_release(
                        family='family' + 'x' * 250 if wide else 'family',
                        systems=(identifier('large', 244), identifier('small', 244)),
                        cases=1,
                    )
                    await publish_release(ledger, release)
                    await ledger.activate(
                        Context(command_id='activate', principal='admin', now=1),
                        release.evaluation.family,
                        0,
                        release.evaluation.id,
                        release.publication.id,
                        release.points.id,
                    )
                    actor_id = identifier('actor')
                    session_id = identifier('session')
                    await seed_actor(transport, Actor(id=actor_id, created_at=2))
                    await ledger.start_session(
                        Session(
                            id=session_id,
                            actor_id=actor_id,
                            family=release.evaluation.family,
                            created_at=2,
                        )
                    )
                    receipts: list[Receipt] = []
                    withdrawals: list[int] = []
                    version = 0

                    for index in range(81):
                        now = 100 + index * 10
                        lease = await ledger.issue(
                            Context(
                                command_id=f'draw:{index}', principal=actor_id, now=now
                            ),
                            Issue(
                                lease_id=identifier(f'lease:{index}'),
                                session_id=session_id,
                                session_version=version,
                                claim_id=identifier('claim'),
                                claim_version=version,
                                case_key=release.cases[0].key,
                                a_output=release.outputs[0].key,
                                b_output=release.outputs[1].key,
                                expires_at=now + 5_000,
                            ),
                        )
                        receipt = await ledger.accept(
                            Context(
                                command_id=f'vote:{index}',
                                principal=actor_id,
                                now=now + 1,
                            ),
                            Accept(
                                lease_id=lease.id,
                                acknowledgment=identifier(f'ack:{index}'),
                                choice='a',
                                attribution=tuple(
                                    Attribution(
                                        version=identifier('key'),
                                        kind=kind,
                                        tag=identifier(f'tag:{kind}'),
                                        expires_at=now + 1 + RETENTION_MS,
                                    )
                                    for kind in ('exact', 'prefix')
                                ),
                            ),
                        )
                        receipts.append(receipt)
                        version += 2

                        if index != 80:
                            withdrawn = await ledger.withdraw(
                                Context(
                                    command_id=f'withdraw:{index}',
                                    principal=actor_id,
                                    now=now + 2,
                                ),
                                session_id,
                                version,
                                receipt.seq,
                            )
                            withdrawals.append(withdrawn.seq)
                            version += 1

                    filters = ReceiptFilter(family=release.evaluation.family)
                    pages: list[InvestigationPage] = []
                    observed: list[InvestigatedReceipt] = []
                    after = 0

                    while True:
                        page = await ledger.investigation.receipts(
                            filters, 1_000, after
                        )
                        pages.append(page)
                        observed.extend(page.rows)
                        assert (
                            sum(
                                len(row.model_dump_json().encode()) for row in page.rows
                            )
                            <= 1_048_576
                        )

                        if page.after is None:
                            break

                        assert page.after == page.rows[-1].observation.seq > after
                        after = page.after

                    assert tuple(row.observation for row in observed) == tuple(receipts)
                    assert [row.withdrawn for row in observed] == [*withdrawals, None]
                    assert all(row.claim_version == version for row in observed)
                    assert all(
                        not row.selected and not row.eligible for row in observed[:-1]
                    )
                    assert observed[-1].selected and observed[-1].eligible
                    assert pages[0].after is not None

                    if wide:
                        assert len(pages[0].rows) < 75
                        assert (
                            sum(
                                len(row.model_dump_json().encode())
                                for row in (*pages[0].rows, pages[1].rows[0])
                            )
                            > 1_048_576
                        )
                    else:
                        assert tuple(row.observation for row in pages[0].rows) == tuple(
                            receipts[:75]
                        )

                    active = await ledger.investigation.receipts(
                        filters.model_copy(update={'status': 'active'}), 1_000
                    )
                    assert tuple(row.observation for row in active.rows) == (
                        receipts[-1],
                    )

                    withdrawn_sequences: list[int] = []
                    after = 0

                    while True:
                        withdrawn_page = await ledger.investigation.receipts(
                            filters.model_copy(update={'status': 'withdrawn'}),
                            1_000,
                            after,
                        )
                        withdrawn_sequences.extend(
                            row.observation.seq for row in withdrawn_page.rows
                        )

                        if withdrawn_page.after is None:
                            break

                        assert withdrawn_page.after > after
                        after = withdrawn_page.after

                    assert withdrawn_sequences == [row.seq for row in receipts[:-1]]
                    exact_filter = ReceiptFilter(
                        family=release.evaluation.family,
                        actor_id=actor_id,
                        session_id=session_id,
                        evaluation_id=release.evaluation.id,
                        status='withdrawn',
                        recorded_from=receipts[40].recorded_at,
                        recorded_before=receipts[41].recorded_at,
                    )
                    exact_page = await ledger.investigation.receipts(
                        exact_filter, 1_000
                    )
                    assert tuple(row.observation for row in exact_page.rows) == (
                        receipts[40],
                    )
                    mismatched = await ledger.investigation.receipts(
                        exact_filter.model_copy(
                            update={'evaluation_id': 'other-evaluation'}
                        ),
                        1_000,
                    )
                    assert mismatched.rows == ()

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
                    assert await ledger.freshness(before.id) == 'current'
                    assert await ledger.freshness('unknown-export') == 'unknown'
                    refreshed = make_release(suffix='next')
                    await publish_presentation(ledger, refreshed)

                    await ledger.activate(
                        Context(command_id='activate:next', principal='admin', now=30),
                        'writing',
                        before.family_revision,
                        refreshed.evaluation.id,
                        refreshed.publication.id,
                        refreshed.points.id,
                    )
                    after = await ledger.freeze('after', 'writing', 40)

                    assert after.analysis_sequence == before.analysis_sequence
                    assert after.family_revision > before.family_revision
                    assert after.evaluation_id == before.evaluation_id
                    assert after.policy == before.policy
                    assert after.publication_id == refreshed.publication.id
                    assert after.point_id == before.point_id
                    assert await ledger.freshness(before.id) == 'current'
                    assert await ledger.resume('actor', 'session', 41) == lease
                    assert await ledger.freeze('before', 'writing', 50) == before
                    assert (
                        before.namespace == 'test-deployment'
                        and before.schema_version == 1
                    )

                    accepted = await ledger.accept(
                        Context(command_id='accept', principal='actor', now=60),
                        Accept(
                            lease_id=lease.id,
                            acknowledgment='ack',
                            choice='b',
                            attribution=attribution(60),
                        ),
                    )

                    assert accepted.publication_id == make_release().publication.id
                    assert accepted.point_id == make_release().points.id
                    assert (
                        await ledger.freeze('voted', 'writing', 70)
                    ).analysis_sequence > after.analysis_sequence
                    assert await ledger.freshness(before.id) == 'stale'
                    assert await ledger.freshness(after.id) == 'stale'
                    assert await ledger.freshness('voted') == 'current'

            asyncio.run(scenario())

        def test_policy_activation_marks_the_previous_analysis_stale(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    original = await prepare(ledger, transport)
                    before = await ledger.freeze('before-policy', 'writing', 20)
                    policy = original.policy.model_copy(
                        update={
                            'method': original.policy.method.model_copy(
                                update={'l2_penalty': 0.02}
                            )
                        }
                    )
                    changed = make_release(policy=policy)
                    await publish_release(ledger, changed)

                    await ledger.activate(
                        Context(command_id='policy-change', principal='admin', now=30),
                        'writing',
                        before.family_revision,
                        changed.evaluation.id,
                        changed.publication.id,
                        changed.points.id,
                    )

                    assert await ledger.freshness(before.id) == 'stale'
                    current = await ledger.freeze('after-policy', 'writing', 31)
                    assert current.policy_id != before.policy_id
                    assert await ledger.freshness(current.id) == 'current'

            asyncio.run(scenario())

        @pytest.mark.parametrize(
            'failing_case', [0, 1], ids=['accepted-case', 'pending-case']
        )
        def test_gate_refresh_updates_eligibility_and_permanently_revokes_stale_leases(
            self, d1_runtime: D1Runtime, failing_case: int
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    original = await prepare(ledger, transport)
                    first = await ledger.issue(
                        Context(command_id='draw:0', principal='actor', now=10), issue()
                    )
                    receipt = await ledger.accept(
                        Context(command_id='vote', principal='actor', now=20),
                        Accept(
                            lease_id=first.id,
                            acknowledgment='ack',
                            choice='a',
                            attribution=attribution(20),
                        ),
                    )
                    pending = await ledger.issue(
                        Context(command_id='draw:1', principal='actor', now=21),
                        issue(lease='pending', case=1).model_copy(
                            update={'session_version': 2}
                        ),
                    )
                    before = await ledger.freeze('before-gate', 'writing', 22)
                    gated = await publish(
                        ledger, suffix='gated', failing_case=failing_case
                    )

                    changed = await ledger.activate(
                        Context(command_id='gate', principal='admin', now=30),
                        'writing',
                        before.family_revision,
                        gated.evaluation.id,
                        gated.publication.id,
                        gated.points.id,
                    )

                    assert await transport.rows(
                        'SELECT receipt_seq FROM eligible_claims'
                    ) == ([] if failing_case == 0 else [{'receipt_seq': receipt.seq}])
                    assert await transport.rows('SELECT count FROM coverage') == [
                        {'count': 0 if failing_case == 0 else 1}
                    ]
                    assert await ledger.resume('actor', 'session', 31) == (
                        pending if failing_case == 0 else None
                    )

                    await ledger.activate(
                        Context(
                            command_id='restore-evaluation', principal='admin', now=40
                        ),
                        'writing',
                        changed.seq,
                        original.evaluation.id,
                        original.publication.id,
                        original.points.id,
                    )

                    assert await transport.rows(
                        'SELECT receipt_seq FROM eligible_claims'
                    ) == [{'receipt_seq': receipt.seq}]
                    assert await transport.rows('SELECT count FROM coverage') == [
                        {'count': 1}
                    ]
                    assert await ledger.resume('actor', 'session', 41) == (
                        pending if failing_case == 0 else None
                    )

                    if failing_case == 1:
                        with pytest.raises(CommandRejected, match='^lease_stale$'):
                            await ledger.accept(
                                Context(
                                    command_id='stale-vote', principal='actor', now=42
                                ),
                                Accept(
                                    lease_id=pending.id,
                                    acknowledgment='stale',
                                    choice='a',
                                    attribution=attribution(42),
                                ),
                            )

                        assert await transport.rows(
                            "SELECT status FROM pair_leases WHERE id = 'pending'"
                        ) == [{'status': 'revoked'}]

            asyncio.run(scenario())

    class TestRenewalCredential:
        @pytest.mark.parametrize('state', ['active', 'replaced', 'revoked'])
        def test_recovery_respects_the_original_expiry_and_exact_replacement(
            self, d1_runtime: D1Runtime, state: str
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await transport.reset()
                    await transport.bootstrap(
                        BootstrapConfiguration(
                            namespace='deployment',
                            quotas=tuple(
                                Quota(
                                    operation=operation,
                                    scope='actor',
                                    window_ms=1_000,
                                    limit=10,
                                )
                                for operation in ('admit', 'renew')
                            ),
                            attribution_ms=RETENTION_MS,
                        )
                    )
                    await ledger.admit(
                        Context(command_id='new', principal='bootstrap', now=10),
                        Admission(
                            operation='new',
                            context_digest='bootstrap',
                            proof_digest='new-proof',
                            actor_id='actor',
                            credential_id='original',
                            rate_subject='rate',
                            expires_at=1_000,
                            challenge_until=100,
                        ),
                    )
                    original = await ledger.credential('original', 11)
                    assert original is not None
                    renewal = Admission(
                        operation='renew',
                        context_digest='original-cookie',
                        proof_digest='renewal-proof',
                        original_credential='original',
                        actor_id='actor',
                        credential_id='replacement',
                        rate_subject='rate',
                        expires_at=2_000_000,
                        challenge_until=10_000,
                    )
                    expected = original

                    if state == 'replaced':
                        result = await ledger.admit(
                            Context(command_id='renew', principal='actor', now=20),
                            renewal,
                        )
                        expected = original.model_copy(
                            update={'revoked_at': 20, 'renewal_command': 'renew'}
                        )

                        assert (
                            await ledger.resolve_admission(
                                Context(
                                    command_id='renew',
                                    principal='actor',
                                    now=600_999,
                                ),
                                'renew',
                                'original-cookie',
                                'original',
                            )
                            == result
                        )
                    elif state == 'revoked':
                        await transport.execute(
                            Statement(
                                sql='UPDATE credentials SET revoked_at = 20 '
                                "WHERE id = 'original'"
                            )
                        )

                    tables = ('credentials', 'admission_results', 'quota_windows')
                    before = {
                        table: await transport.rows(f'SELECT * FROM {table}')
                        for table in tables
                    }

                    for now in (999, 1_000, 600_999, 601_000, 601_001):
                        for command in ('renew', 'different'):
                            recovered = await ledger.renewal_credential(
                                'original', command, now
                            )
                            allowed = now < 601_000 and (
                                state == 'active'
                                or (state == 'replaced' and command == 'renew')
                            )
                            assert recovered == (expected if allowed else None), (
                                state,
                                command,
                                now,
                            )

                    assert (
                        await ledger.renewal_credential('absent', 'renew', 30) is None
                    )
                    assert await ledger.credential('original', 999) == (
                        original if state == 'active' else None
                    )
                    assert await ledger.credential('original', 1_000) is None

                    if state != 'replaced':
                        assert (
                            await ledger.resolve_admission(
                                Context(
                                    command_id='renew', principal='actor', now=1_000
                                ),
                                'renew',
                                'original-cookie',
                                'original',
                            )
                            is None
                        )

                    with pytest.raises(CommandRejected, match='^credential_context$'):
                        await ledger.admit(
                            Context(
                                command_id='fresh',
                                principal='actor',
                                now=1_000 if state == 'active' else 30,
                            ),
                            renewal.model_copy(
                                update={
                                    'proof_digest': 'fresh-proof',
                                    'credential_id': 'fresh',
                                }
                            ),
                        )

                    assert {
                        table: await transport.rows(f'SELECT * FROM {table}')
                        for table in tables
                    } == before

            asyncio.run(scenario())

    class TestAdmit:
        @pytest.mark.parametrize('operation', ['issue', 'accept'])
        def test_renewal_preserves_actor_history_and_cross_family_quota(
            self, d1_runtime: D1Runtime, operation: str
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await transport.reset()
                    await transport.bootstrap(
                        BootstrapConfiguration(
                            namespace='deployment',
                            quotas=tuple(
                                quota.model_copy(
                                    update={'limit': 1}
                                    if quota.operation == operation
                                    else {}
                                )
                                for quota in QUOTAS
                            ),
                            attribution_ms=RETENTION_MS,
                        )
                    )

                    for family in ('writing', 'other'):
                        release = await publish(ledger, family=family)
                        await ledger.activate(
                            Context(
                                command_id=f'activate:{family}',
                                principal='admin',
                                now=1,
                            ),
                            family,
                            0,
                            release.evaluation.id,
                            release.publication.id,
                            release.points.id,
                        )

                    admitted = await ledger.admit(
                        Context(command_id='admit', principal='bootstrap', now=2),
                        Admission(
                            operation='new',
                            context_digest='bootstrap',
                            proof_digest='admit-proof',
                            actor_id='actor',
                            credential_id='original',
                            rate_subject='rate',
                            expires_at=10_000,
                            challenge_until=1_000,
                        ),
                    )
                    await ledger.open_session('actor', 'first', 'writing', 3)
                    lease = await ledger.issue(
                        Context(command_id='first-draw', principal='actor', now=10),
                        issue(session='first'),
                    )
                    receipt = await ledger.accept(
                        Context(command_id='first-vote', principal='actor', now=20),
                        Accept(
                            lease_id=lease.id,
                            acknowledgment='first',
                            choice='a',
                            attribution=attribution(20),
                        ),
                    )

                    renewed = await ledger.admit(
                        Context(command_id='renew', principal='actor', now=30),
                        Admission(
                            operation='renew',
                            context_digest='original-cookie',
                            proof_digest='renew-proof',
                            original_credential='original',
                            actor_id='actor',
                            credential_id='replacement',
                            rate_subject='rate',
                            expires_at=20_000,
                            challenge_until=2_000,
                        ),
                    )

                    assert renewed.actor_id == admitted.actor_id == receipt.actor_id
                    assert await ledger.credential('original', 31) is None
                    credential = await ledger.credential('replacement', 31)
                    assert credential is not None and credential.actor_id == 'actor'
                    await ledger.open_session('actor', 'second', 'other', 31)
                    context = Context(
                        command_id='second-draw', principal='actor', now=32
                    )
                    proposed = issue(session='second', lease='second')

                    if operation == 'issue':
                        with pytest.raises(CommandRejected, match='^quota_exhausted$'):
                            await ledger.issue(context, proposed)
                    else:
                        second = await ledger.issue(context, proposed)

                        with pytest.raises(CommandRejected, match='^quota_exhausted$'):
                            await ledger.accept(
                                Context(
                                    command_id='second-vote',
                                    principal='actor',
                                    now=33,
                                ),
                                Accept(
                                    lease_id=second.id,
                                    acknowledgment='second',
                                    choice='b',
                                    attribution=attribution(33),
                                ),
                            )

                    await ledger.admit(
                        Context(
                            command_id='other-admit',
                            principal='other-bootstrap',
                            now=34,
                        ),
                        Admission(
                            operation='new',
                            context_digest='other-bootstrap',
                            proof_digest='other-proof',
                            actor_id='other',
                            credential_id='other-credential',
                            rate_subject='other-rate',
                            expires_at=10_000,
                            challenge_until=1_000,
                        ),
                    )
                    await ledger.open_session('other', 'other-session', 'other', 35)
                    independent = await ledger.issue(
                        Context(command_id='other-draw', principal='other', now=36),
                        issue(session='other-session', lease='independent'),
                    )
                    await ledger.accept(
                        Context(command_id='other-vote', principal='other', now=37),
                        Accept(
                            lease_id=independent.id,
                            acknowledgment='independent',
                            choice='b',
                            attribution=attribution(37),
                        ),
                    )

                    assert await transport.rows(
                        'SELECT actor_id, choice FROM vote_receipts ORDER BY seq'
                    ) == [
                        {'actor_id': 'actor', 'choice': 'a'},
                        {'actor_id': 'other', 'choice': 'b'},
                    ]
                    assert await transport.rows(
                        'SELECT subject, consumed FROM quota_windows '
                        'WHERE operation = ? ORDER BY subject',
                        operation,
                    ) == [
                        {'subject': 'actor', 'consumed': 1},
                        {'subject': 'other', 'consumed': 1},
                    ]
                    frozen = await ledger.freeze('actor-history', 'writing', 40)
                    actors = await ledger.page(frozen.id, 'actors')
                    assert actors.rows == [{'id': 'actor', 'created_at': 2}]

            asyncio.run(scenario())

    class TestIssue:
        def test_pending_limit_counts_other_families_and_excludes_expired_leases(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await prepare(
                        ledger,
                        transport,
                        quotas=(
                            Quota(
                                operation='issue',
                                scope='actor',
                                window_ms=1_000,
                                limit=100,
                            ),
                        ),
                    )
                    for index in range(5):
                        family = f'family-{index}'
                        release = await publish(ledger, family=family)
                        await ledger.activate(
                            Context(
                                command_id=f'activate:{family}',
                                principal='admin',
                                now=4,
                            ),
                            family,
                            0,
                            release.evaluation.id,
                            release.publication.id,
                            release.points.id,
                        )
                        await ledger.start_session(
                            Session(
                                id=f'session:{index}',
                                actor_id='actor',
                                family=family,
                                created_at=5,
                            )
                        )

                        if index < 4:
                            await ledger.issue(
                                Context(
                                    command_id=f'draw:{index}',
                                    principal='actor',
                                    now=10,
                                ),
                                issue(
                                    session=f'session:{index}', lease=f'lease:{index}'
                                ).model_copy(
                                    update={'expires_at': 100 if index == 0 else 10_000}
                                ),
                            )

                    tables = (
                        'claims',
                        'evidence_events',
                        'lease_revocations',
                        'pair_leases',
                        'quota_windows',
                        'session_state',
                        'sessions',
                    )
                    before = {
                        table: await transport.rows(f'SELECT * FROM {table}')
                        for table in tables
                    }
                    assert (
                        sum(
                            TypeAdapter(int).validate_python(row['consumed'])
                            for row in before['quota_windows']
                        )
                        == 4
                    )
                    proposed = issue(session='session:4', lease='fifth')

                    with pytest.raises(CommandRejected, match='^pending_limit$'):
                        await ledger.issue(
                            Context(command_id='fifth', principal='actor', now=20),
                            proposed,
                        )

                    assert {
                        table: await transport.rows(f'SELECT * FROM {table}')
                        for table in tables
                    } == before

                    await seed_actor(transport, Actor(id='other', created_at=21))
                    await ledger.open_session('other', 'independent', 'family-4', 22)
                    independent = await ledger.issue(
                        Context(command_id='independent', principal='other', now=30),
                        issue(session='independent', lease='independent'),
                    )
                    assert independent.actor_id == 'other'

                    accepted = await ledger.issue(
                        Context(command_id='after-expiry', principal='actor', now=100),
                        proposed,
                    )

                    assert accepted.id == 'fifth'
                    assert accepted.actor_id == 'actor'
                    assert await transport.rows(
                        'SELECT count(*) AS live FROM pair_leases '
                        "WHERE actor_id = 'actor' AND status = 'pending' "
                        'AND expires_at > 100'
                    ) == [{'live': 4}]

            asyncio.run(scenario())

        def test_expired_displaced_lease_clears_both_old_pointers_atomically(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await prepare(
                        ledger,
                        transport,
                        quotas=tuple(
                            Quota(
                                operation=operation,
                                scope='actor',
                                window_ms=86_400_000,
                                limit=2,
                            )
                            for operation in ('issue', 'accept')
                        ),
                    )
                    original = await ledger.issue(
                        Context(command_id='first-draw', principal='actor', now=10),
                        issue(),
                    )
                    await ledger.start_session(
                        Session(
                            id='next-session',
                            actor_id='actor',
                            family='writing',
                            created_at=20,
                        )
                    )

                    successor = await ledger.issue(
                        Context(
                            command_id='next-draw',
                            principal='actor',
                            now=original.expires_at,
                        ),
                        issue(
                            session='next-session', lease='next-lease', case=1
                        ).model_copy(
                            update={'expires_at': original.expires_at + 5_000}
                        ),
                    )

                    old_session = await ledger.read.session('actor', 'session')
                    assert old_session is not None
                    assert (
                        old_session.current_lease is None and old_session.version == 2
                    )
                    assert await transport.rows(
                        'SELECT version, current_lease FROM claims WHERE id = ?',
                        original.claim_id,
                    ) == [{'version': 2, 'current_lease': None}]
                    assert (
                        await ledger.resume('actor', 'next-session', 10_001)
                        == successor
                    )

                    with pytest.raises(CommandRejected, match='^lease_stale$'):
                        await ledger.accept(
                            Context(
                                command_id='stale-vote', principal='actor', now=10_001
                            ),
                            Accept(
                                lease_id=original.id,
                                acknowledgment='stale-ack',
                                choice='a',
                                attribution=attribution(10_001),
                            ),
                        )

                    assert await transport.rows(
                        'SELECT operation, consumed FROM quota_windows'
                    ) == [{'operation': 'issue', 'consumed': 2}]
                    receipt = await ledger.accept(
                        Context(command_id='next-vote', principal='actor', now=10_002),
                        Accept(
                            lease_id=successor.id,
                            acknowledgment='next-ack',
                            choice='b',
                            attribution=attribution(10_002),
                        ),
                    )
                    assert receipt.lease_id == successor.id
                    assert receipt.case_key == case_key(1)

            asyncio.run(scenario())

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
                            attribution=attribution(20),
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
                        await seed_actor(transport, Actor(id=actor, created_at=30))

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
                            'a_output': output_key(0, 'small')
                            if swapped
                            else output_key(0, 'large'),
                            'b_output': output_key(0, 'large')
                            if swapped
                            else output_key(0, 'small'),
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
                                attribution=attribution(now + 1),
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
        @pytest.mark.parametrize(
            ('mutation', 'rejection'),
            [
                ('missing', 'attribution_context'),
                ('missing-prefix', 'attribution_context'),
                ('duplicate-exact', 'attribution_context'),
                ('mixed-versions', 'attribution_context'),
                ('wrong-expiry', 'attribution_expiry'),
            ],
            ids=lambda value: value,
        )
        def test_invalid_attribution_rolls_back_before_the_valid_vote(
            self, d1_runtime: D1Runtime, mutation: str, rejection: str
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await prepare(
                        ledger,
                        transport,
                        quotas=tuple(
                            Quota(
                                operation=operation,
                                scope='actor',
                                window_ms=1_000,
                                limit=1,
                            )
                            for operation in ('issue', 'accept')
                        ),
                    )
                    lease = await ledger.issue(
                        Context(command_id='issue', principal='actor', now=10), issue()
                    )
                    tags = attribution(20)
                    invalid: tuple[Attribution, ...]

                    match mutation:
                        case 'missing':
                            invalid = ()
                        case 'missing-prefix':
                            invalid = tags[:1]
                        case 'duplicate-exact':
                            invalid = (tags[0], tags[0])
                        case 'mixed-versions':
                            invalid = (
                                tags[0],
                                tags[1].model_copy(update={'version': 'other-key'}),
                            )
                        case 'wrong-expiry':
                            invalid = (
                                tags[0],
                                tags[1].model_copy(
                                    update={'expires_at': tags[1].expires_at + 1}
                                ),
                            )
                        case _:
                            raise AssertionError(mutation)

                    tables = (
                        'evidence_events',
                        'vote_receipts',
                        'claims',
                        'pair_leases',
                        'session_state',
                        'coverage',
                        'quota_windows',
                        'network_attribution',
                    )
                    before = {
                        table: await transport.rows(f'SELECT * FROM {table}')
                        for table in tables
                    }
                    context = Context(command_id='vote', principal='actor', now=20)
                    request = Accept(
                        lease_id=lease.id,
                        acknowledgment='ack',
                        choice='a',
                        attribution=invalid,
                    )

                    with pytest.raises(CommandRejected, match=f'^{rejection}$'):
                        await ledger.accept(context, request)

                    assert {
                        table: await transport.rows(f'SELECT * FROM {table}')
                        for table in tables
                    } == before

                    receipt = await ledger.accept(
                        context, request.model_copy(update={'attribution': tags})
                    )

                    assert receipt.acknowledgment == 'ack'
                    assert await transport.rows(
                        'SELECT operation, consumed FROM quota_windows '
                        'ORDER BY operation'
                    ) == [
                        {'operation': 'accept', 'consumed': 1},
                        {'operation': 'issue', 'consumed': 1},
                    ]
                    assert await transport.rows('SELECT count FROM coverage') == [
                        {'count': 1}
                    ]

            asyncio.run(scenario())

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
                            Accept(
                                lease_id=lease.id,
                                acknowledgment='ack',
                                choice='a',
                                attribution=attribution(context.now),
                            ),
                        ),
                        other.accept(
                            context,
                            Accept(
                                lease_id=lease.id,
                                acknowledgment='ack',
                                choice='b',
                                attribution=attribution(context.now),
                            ),
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
                        lease_id=lease.id,
                        acknowledgment='ack',
                        choice='a',
                        attribution=attribution(20),
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
                    receipts: list[Receipt] = []
                    requests: list[Accept] = []

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
                    await prepare(ledger, transport, quotas=quotas)
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
                        lease_id=lease.id,
                        acknowledgment='ack',
                        choice='a',
                        attribution=attribution(20),
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
                        Accept(
                            lease_id=lease.id,
                            acknowledgment='ack',
                            choice='a',
                            attribution=attribution(20),
                        ),
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
                            Accept(
                                lease_id=lease.id,
                                acknowledgment='ack',
                                choice='a',
                                attribution=attribution(21),
                            ),
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
                            attribution=attribution(20),
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
                                attribution=attribution(now + 1),
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

    class TestFreeze:
        def test_frozen_and_presentation_metadata_reject_unsafe_family_names(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    release = await prepare(ledger, transport)
                    frozen = await ledger.freeze('family-name', 'writing', 10)
                    assert (
                        Export.model_validate_json(frozen.model_dump_json()) == frozen
                    )

                    for family in (
                        '../outside',
                        '/absolute',
                        'writing/../../outside',
                        'writing\n',
                        'writing\r\n',
                        'Writing',
                        'x' * 257,
                        '',
                    ):
                        exported = frozen.model_dump(mode='json')
                        exported['family'] = family
                        publication = release.publication.model_dump(mode='json')
                        publication['presentation']['family'] = family

                        with pytest.raises(ValidationError, match='family'):
                            Export.model_validate(exported)

                        with pytest.raises(ValidationError, match='family'):
                            Publication.model_validate(publication)

                    assert await ledger.freeze(frozen.id, 'writing', 20) == frozen

            asyncio.run(scenario())

        def test_frozen_pages_preserve_receipt_cutoff_and_inventory_after_later_changes(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await prepare(ledger, transport)
                    lease = await ledger.issue(
                        Context(command_id='first-draw', principal='actor', now=10),
                        issue(),
                    )
                    first = await ledger.accept(
                        Context(command_id='first-vote', principal='actor', now=11),
                        Accept(
                            lease_id=lease.id,
                            acknowledgment='first',
                            choice='a',
                            attribution=attribution(11),
                        ),
                    )
                    frozen = await ledger.freeze('frozen', 'writing', 20)
                    before = await export_rows(ledger, frozen.id, limit=1)

                    later_lease = await ledger.issue(
                        Context(command_id='later-draw', principal='actor', now=21),
                        issue(case=1, lease='later').model_copy(
                            update={'session_version': 2}
                        ),
                    )
                    later = await ledger.accept(
                        Context(command_id='later-vote', principal='actor', now=22),
                        Accept(
                            lease_id=later_lease.id,
                            acknowledgment='later',
                            choice='b',
                            attribution=attribution(22),
                        ),
                    )
                    refreshed = await publish(ledger, suffix='next', cases=4)
                    state = await ledger.read.family('writing')
                    assert state is not None
                    await ledger.activate(
                        Context(command_id='activate:next', principal='admin', now=30),
                        'writing',
                        state.revision,
                        refreshed.evaluation.id,
                        refreshed.publication.id,
                        refreshed.points.id,
                    )
                    after = await export_rows(ledger, frozen.id, limit=3)
                    current = await ledger.freeze('current', 'writing', 40)
                    current_rows = await export_rows(ledger, current.id, limit=2)

                    assert after == before
                    assert frozen.sections == {
                        section: len(rows) for section, rows in before.items()
                    }
                    assert current.sections == {
                        section: len(rows) for section, rows in current_rows.items()
                    }
                    assert await ledger.freeze(frozen.id, 'writing', 50) == frozen
                    assert frozen.namespace == 'test-deployment'
                    assert frozen.schema_version == 1
                    assert [row['seq'] for row in before['receipts']] == [first.seq]
                    assert [row['seq'] for row in current_rows['receipts']] == [
                        first.seq,
                        later.seq,
                    ]
                    assert (
                        len(before['outputs']) == 6
                        and len(current_rows['outputs']) == 8
                    )

            asyncio.run(scenario())

    class TestMaintenance:
        @pytest.mark.parametrize(
            'activation', [False, True], ids=['expiry', 'activation']
        )
        def test_abandoned_browsing_state_expires_without_erasing_audit_targets(
            self, d1_runtime: D1Runtime, activation: bool
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await prepare(ledger, transport)
                    lease = await ledger.issue(
                        Context(command_id='issue', principal='actor', now=10), issue()
                    )
                    targets = []

                    if activation:
                        changed = await publish(ledger, failing_case=0)
                        state = await ledger.read.family('writing')
                        assert state is not None
                        await ledger.activate(
                            Context(command_id='change', principal='admin', now=20),
                            'writing',
                            state.revision,
                            changed.evaluation.id,
                            changed.publication.id,
                            changed.points.id,
                        )
                        targets = await transport.rows('SELECT * FROM event_targets')
                        assert targets[0]['claim_id'] == lease.claim_id
                        assert targets[0]['lease_id'] == lease.id
                    else:
                        await ledger.maintenance.expire(lease.expires_at - 1)
                        assert await ledger.resume('actor', 'session', 9_999) == lease

                    await ledger.maintenance.expire(lease.expires_at)

                    assert await ledger.resume('actor', 'session', 10_000) is None
                    assert await transport.rows('SELECT status FROM pair_leases') == [
                        {'status': 'revoked'}
                    ]
                    assert await transport.rows(
                        'SELECT version, current_lease FROM claims'
                    ) == [{'version': 2, 'current_lease': None}]
                    owned = await ledger.read.session('actor', 'session')
                    assert owned is not None
                    assert owned.version == 2 and owned.current_lease is None

                    prune_at = lease.expires_at + 7 * 86_400_000
                    await ledger.maintenance.expire(prune_at - 1)
                    assert await transport.rows('SELECT id FROM pair_leases') == [
                        {'id': lease.id}
                    ]
                    await ledger.maintenance.expire(prune_at)

                    assert await transport.rows('SELECT * FROM pair_leases') == []
                    assert await transport.rows('SELECT * FROM claims') == []
                    assert (
                        await transport.rows('SELECT * FROM event_targets') == targets
                    )

                    await ledger.maintenance.expire(2 + 30 * 86_400_000 - 1)
                    assert await ledger.read.session('actor', 'session') is not None
                    await ledger.maintenance.expire(2 + 30 * 86_400_000)

                    assert await ledger.read.session('actor', 'session') is None
                    assert await transport.rows('SELECT * FROM actors') == []
                    assert (
                        await transport.rows('SELECT * FROM event_targets') == targets
                    )

            asyncio.run(scenario())

        def test_sixty_day_tag_expiry_preserves_receipt_replay_and_numerical_evidence(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await prepare(
                        ledger,
                        transport,
                        quotas=tuple(
                            Quota(
                                operation=operation,
                                scope='actor',
                                window_ms=1_000,
                                limit=10,
                            )
                            for operation in ('issue', 'accept')
                        ),
                    )
                    lease = await ledger.issue(
                        Context(command_id='issue', principal='actor', now=10), issue()
                    )
                    context = Context(command_id='accept', principal='actor', now=20)
                    request = Accept(
                        lease_id=lease.id,
                        acknowledgment='ack',
                        choice='a',
                        attribution=attribution(context.now),
                    )
                    receipt = await ledger.accept(context, request)
                    frozen = await ledger.freeze('before-expiry', 'writing', 21)
                    before = await export_rows(ledger, frozen.id, limit=2)
                    expiry = context.now + RETENTION_MS
                    filters = ReceiptFilter(
                        family='writing',
                        actor_id='actor',
                        session_id='session',
                        evaluation_id=receipt.evaluation_id,
                        recorded_from=context.now,
                        recorded_before=context.now + 1,
                        status='active',
                    )
                    page = await ledger.investigation.receipts(filters, expiry - 1)

                    assert page.after is None
                    assert tuple(row.observation for row in page.rows) == (receipt,)
                    investigated = page.rows[0]
                    assert investigated.claim_version == 2
                    assert investigated.selected and investigated.eligible
                    assert investigated.network == request.attribution
                    assert investigated.withdrawn is investigated.excluded is None
                    assert investigated.superseded is None

                    for field in ('actor_id', 'session_id'):
                        hidden = await ledger.investigation.receipts(
                            filters.model_copy(update={field: 'other'}), expiry - 1
                        )
                        assert hidden.rows == (), field

                    for tag in request.attribution:
                        lookup = filters.model_copy(
                            update={
                                'network': NetworkLookup(
                                    version=tag.version, kind=tag.kind, tag=tag.tag
                                )
                            }
                        )
                        live = await ledger.investigation.receipts(lookup, expiry - 1)
                        expired = await ledger.investigation.receipts(lookup, expiry)

                        assert live.rows == page.rows, tag.kind
                        assert expired.rows == (), tag.kind

                    expired_page = await ledger.investigation.receipts(filters, expiry)
                    assert expired_page.rows[0].observation == receipt
                    assert expired_page.rows[0].network == ()

                    await ledger.maintenance.expire(expiry - 1)

                    assert await transport.rows(
                        'SELECT tag_kind AS kind, tag, expires_at '
                        'FROM network_attribution ORDER BY tag_kind'
                    ) == [
                        {
                            'kind': kind,
                            'tag': f'attribution:{kind}',
                            'expires_at': expiry,
                        }
                        for kind in ('exact', 'prefix')
                    ]
                    assert await ledger.maintenance.attribution_lag(expiry + 5) == 5
                    assert await transport.rows('SELECT * FROM pair_leases') == []

                    await ledger.maintenance.expire(expiry)
                    replay = await ledger.accept(
                        context.model_copy(update={'now': expiry + 1}), request
                    )
                    resolved = await ledger.resolve_receipt(
                        context.model_copy(update={'now': expiry + 2}),
                        VoteMeaning(lease_id=lease.id, choice='a'),
                    )

                    assert replay == resolved == receipt
                    assert (
                        await transport.rows('SELECT * FROM network_attribution') == []
                    )
                    assert await ledger.maintenance.attribution_lag(expiry + 5) == 0
                    assert await transport.rows('SELECT * FROM quota_windows') == []
                    assert await ledger.read.session('actor', 'session') is not None
                    assert await transport.rows('SELECT id FROM actors') == [
                        {'id': 'actor'}
                    ]
                    assert await export_rows(ledger, frozen.id, limit=1) == before
                    current = await ledger.freeze('after-expiry', 'writing', expiry + 3)
                    after = await export_rows(ledger, current.id, limit=3)
                    assert after['receipts'] == before['receipts']
                    assert after['events'] == before['events']
                    assert 'attribution:exact' not in json.dumps(after)
                    assert 'attribution:prefix' not in json.dumps(after)

            asyncio.run(scenario())

        def test_cleanup_is_bounded_and_preserves_fresh_anonymous_actors(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await transport.reset()
                    await transport.bootstrap(
                        BootstrapConfiguration(
                            namespace='deployment',
                            quotas=(),
                            attribution_ms=RETENTION_MS,
                        )
                    )

                    for index in range(251):
                        await seed_actor(
                            transport, Actor(id=f'expired:{index:03}', created_at=0)
                        )

                    await seed_actor(transport, Actor(id='fresh', created_at=1))

                    await ledger.maintenance.expire(30 * 86_400_000)

                    assert await transport.rows(
                        'SELECT id FROM actors ORDER BY id'
                    ) == [
                        {'id': 'expired:250'},
                        {'id': 'fresh'},
                    ]

                    await ledger.maintenance.expire(30 * 86_400_000)

                    assert await transport.rows('SELECT id FROM actors') == [
                        {'id': 'fresh'}
                    ]
                    assert await transport.rows('SELECT * FROM actor_expiry') == [
                        {'actor_id': 'fresh', 'expires_at': 30 * 86_400_000 + 1}
                    ]

            asyncio.run(scenario())

    class TestModerate:
        def test_restoring_historical_exclusion_preserves_the_newer_selected_vote(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await prepare(ledger, transport)
                    lease = await ledger.issue(
                        Context(command_id='draw', principal='actor', now=10), issue()
                    )
                    old = await ledger.accept(
                        Context(command_id='vote:old', principal='actor', now=20),
                        Accept(
                            lease_id=lease.id,
                            acknowledgment='old',
                            choice='a',
                            attribution=attribution(20),
                        ),
                    )
                    await ledger.stage_moderation(
                        Moderation(
                            id='exclude',
                            family='writing',
                            principal='admin',
                            action='exclude',
                            reason='Reviewed synthetic exclusion.',
                            created_at=30,
                            targets=(Target(receipt_seq=old.seq, claim_version=2),),
                        )
                    )
                    await ledger.seal_moderation('exclude', 30)
                    excluded = await ledger.moderate(
                        Context(command_id='exclude', principal='admin', now=30),
                        'exclude',
                        0,
                    )
                    await ledger.start_session(
                        Session(
                            id='next-session',
                            actor_id='actor',
                            family='writing',
                            created_at=40,
                        )
                    )
                    replacement = await ledger.issue(
                        Context(command_id='next-draw', principal='actor', now=40),
                        issue(session='next-session', lease='replacement').model_copy(
                            update={'claim_id': old.claim_id, 'claim_version': 3}
                        ),
                    )
                    latest = await ledger.accept(
                        Context(command_id='vote:new', principal='actor', now=50),
                        Accept(
                            lease_id=replacement.id,
                            acknowledgment='latest',
                            choice='b',
                            attribution=attribution(50),
                        ),
                    )

                    for status, expected in (
                        ('active', (latest,)),
                        ('excluded', (old,)),
                        ('superseded', (old,)),
                    ):
                        page = await ledger.investigation.receipts(
                            ReceiptFilter(family='writing').model_copy(
                                update={'status': status}
                            ),
                            51,
                        )
                        assert (
                            tuple(row.observation for row in page.rows) == expected
                        ), status
                        assert page.rows[0].claim_version == 5

                    await ledger.stage_moderation(
                        Moderation(
                            id='restore',
                            family='writing',
                            principal='admin',
                            action='restore',
                            reason='Reviewed historical correction.',
                            created_at=60,
                            targets=(
                                Target(
                                    receipt_seq=old.seq,
                                    claim_version=5,
                                    exclusion_seq=excluded.seq,
                                ),
                            ),
                        )
                    )
                    await ledger.seal_moderation('restore', 60)

                    await ledger.moderate(
                        Context(command_id='restore', principal='admin', now=60),
                        'restore',
                        0,
                    )

                    assert await transport.rows(
                        'SELECT receipt_seq FROM eligible_claims'
                    ) == [{'receipt_seq': latest.seq}]
                    assert await transport.rows('SELECT count FROM coverage') == [
                        {'count': 1}
                    ]
                    assert await transport.rows(
                        'SELECT excluded, superseded FROM receipt_state '
                        'WHERE receipt_seq = ?',
                        old.seq,
                    ) == [{'excluded': None, 'superseded': latest.seq}]
                    assert await transport.rows(
                        'SELECT choice FROM vote_receipts WHERE seq = ?', old.seq
                    ) == [{'choice': 'a'}]
                    excluded_page = await ledger.investigation.receipts(
                        ReceiptFilter(family='writing', status='excluded'), 61
                    )
                    superseded_page = await ledger.investigation.receipts(
                        ReceiptFilter(family='writing', status='superseded'), 61
                    )
                    assert excluded_page.rows == ()
                    assert tuple(row.observation for row in superseded_page.rows) == (
                        old,
                    )
                    assert superseded_page.rows[0].claim_version == 6
                    assert not superseded_page.rows[0].selected

            asyncio.run(scenario())

        def test_applies_later_chunks_for_the_same_reviewed_historical_claim(
            self, d1_runtime: D1Runtime
        ) -> None:
            async def scenario() -> None:
                async with d1_runtime.client() as (ledger, transport):
                    await prepare(ledger, transport)
                    receipts: list[Receipt] = []
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
                                attribution=attribution(now + 1),
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
                INSERT OR REPLACE INTO moderation_batches(id, family, principal,
                    action, reason, target_count, target_digest, created_at, sealed_at)
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
                        Accept(
                            lease_id=lease.id,
                            acknowledgment='ack',
                            choice='a',
                            attribution=attribution(20),
                        ),
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
