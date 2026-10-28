import asyncio
import json
import socket
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from stat import S_IMODE

import pytest
from pydantic import JsonValue, ValidationError
from typer.testing import CliRunner

from arena_support import admit_actor
from assistant_bench import cli, families, report
from assistant_bench.arena import snapshots
from assistant_bench.arena.exports import (
    FrozenExport,
    capture_export,
    read_export,
    write_export,
)
from assistant_bench.arena.publication import POLICY
from assistant_bench.arena.snapshots import SnapshotAnalysis, analyze_export
from assistant_bench.report import LedgerProvenance
from assistant_bench_arena.investigation import LedgerInvestigation
from assistant_bench_arena.ledger import Ledger
from assistant_bench_arena.ledger_models import (
    Accept,
    Attribution,
    BootstrapConfiguration,
    Context,
    Issue,
    Moderation,
    Point,
    Publication,
    Quota,
    Receipt,
    Session,
    Target,
)
from assistant_bench_arena.models import Choice
from assistant_bench_arena.releases import point_snapshot
from assistant_bench_arena.serialization import canonical, digest
from assistant_bench_arena.transport import CommandRejected
from ledger_fixtures import case_key, make_release, output_key
from ledger_support import D1Runtime, publish_presentation, publish_release
from support import AT


BASE_MS = int(AT.timestamp() * 1_000)
DAY_MS = 86_400_000
RETENTION_MS = 60 * DAY_MS
FAMILY = 'writing-synthetic'
REPORT_FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/report-ledger.json'
SECTIONS = (
    'evaluation',
    'publication',
    'points',
    'policy',
    'cases',
    'systems',
    'outputs',
    'numerics',
    'receipts',
    'events',
    'targets',
    'actors',
    'sessions',
)


@dataclass(frozen=True)
class Captures:
    before: FrozenExport
    after: FrozenExport
    reread: FrozenExport


def close(snapshot: SnapshotAnalysis) -> report.Report:
    return report.close_snapshot(
        snapshot.analysis,
        AT + timedelta(days=2),
        fingerprints=snapshot.fingerprints,
        system_names=snapshot.system_names,
        frozen_fingerprints={},
    )


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


def configuration(namespace: str) -> BootstrapConfiguration:
    return BootstrapConfiguration(
        namespace=namespace,
        quotas=tuple(
            Quota(operation=operation, scope='actor', window_ms=60_000, limit=1_000)
            for operation in ('admit', 'issue', 'accept')
        ),
        attribution_ms=RETENTION_MS,
    )


async def accept_case(
    ledger: Ledger,
    *,
    actor: str,
    session: str,
    index: int,
    ordinal: int,
    choice: Choice,
) -> Receipt:
    command = f'{actor}:{index}'
    now = BASE_MS + 10 + 2 * index
    lease = await ledger.issue(
        Context(command_id=f'draw:{command}', principal=actor, now=now),
        Issue(
            lease_id=f'lease:{command}',
            session_id=session,
            session_version=2 * ordinal,
            claim_id=f'claim:{command}',
            claim_version=0,
            case_key=case_key(index),
            a_output=output_key(index, 'large'),
            b_output=output_key(index, 'small'),
            expires_at=now + 60_000,
        ),
    )
    return await ledger.accept(
        Context(command_id=f'vote:{command}', principal=actor, now=now + 1),
        Accept(
            lease_id=lease.id,
            acknowledgment=f'ack:{command}',
            choice=choice,
            attribution=attribution(now + 1),
        ),
    )


@pytest.fixture(scope='module')
def captures(tmp_path_factory: pytest.TempPathFactory) -> Captures:
    async def scenario(runtime: D1Runtime) -> Captures:
        async with runtime.client() as (ledger, transport):
            await transport.reset()
            await transport.bootstrap(configuration('synthetic-export'))
            release = make_release(family=FAMILY, cases=130, policy=POLICY)
            await publish_release(ledger, release)
            await ledger.activate(
                Context(command_id='activate', principal='admin', now=BASE_MS),
                FAMILY,
                0,
                release.evaluation.id,
                release.publication.id,
                release.points.id,
            )
            for actor in ('owner', 'visitor'):
                await admit_actor(ledger, actor, BASE_MS + 1)

            for session, actor in (
                ('owner:first', 'owner'),
                ('owner:second', 'owner'),
                ('visitor', 'visitor'),
                ('repeat', 'owner'),
            ):
                await ledger.start_session(
                    Session(
                        id=session,
                        actor_id=actor,
                        family=FAMILY,
                        created_at=BASE_MS + 2,
                    )
                )

            choices: tuple[Choice, ...] = ('a', 'b', 'tie', 'both_bad')
            receipts: list[Receipt] = []
            for index in range(130):
                receipts.append(
                    await accept_case(
                        ledger,
                        actor='owner',
                        session='owner:first' if index < 65 else 'owner:second',
                        index=index,
                        ordinal=index % 65,
                        choice=choices[index % 4],
                    )
                )

            for index in range(20):
                await accept_case(
                    ledger,
                    actor='visitor',
                    session='visitor',
                    index=index,
                    ordinal=index,
                    choice='a',
                )

            first = receipts[0]
            later = BASE_MS + DAY_MS + 100
            lease = await ledger.issue(
                Context(command_id='draw:repeat', principal='owner', now=later),
                Issue(
                    lease_id='lease:repeat',
                    session_id='repeat',
                    session_version=0,
                    claim_id='claim:repeat',
                    claim_version=0,
                    case_key=first.case_key,
                    a_output=first.b_output,
                    b_output=first.a_output,
                    expires_at=later + 60_000,
                    repeat_of=first.seq,
                ),
            )
            await ledger.accept(
                Context(command_id='vote:repeat', principal='owner', now=later + 1),
                Accept(
                    lease_id=lease.id,
                    acknowledgment='ack:repeat',
                    choice='b',
                    attribution=attribution(later + 1),
                ),
            )
            header = await ledger.freeze('before', FAMILY, later + 2)
            before = await capture_export(ledger, header)
            await ledger.stage_moderation(
                Moderation(
                    id='exclude',
                    family=FAMILY,
                    principal='admin',
                    action='exclude',
                    reason='Synthetic reviewed exclusion.',
                    created_at=later + 3,
                    targets=(Target(receipt_seq=first.seq, claim_version=2),),
                )
            )
            await ledger.seal_moderation('exclude', later + 3)
            await ledger.moderate(
                Context(command_id='exclude', principal='admin', now=later + 3),
                'exclude',
                0,
            )
            after_header = await ledger.freeze('after', FAMILY, later + 4)
            after = await capture_export(ledger, after_header)

        async with runtime.client() as (ledger, _):
            reread = await capture_export(ledger, header)

        return Captures(before, after, reread)

    directory = tmp_path_factory.mktemp('offline-export-runtime')
    with D1Runtime(directory).owner() as runtime:
        return asyncio.run(scenario(runtime))


class TestCaptureExport:
    def test_captures_every_registered_section_and_retains_the_original_cut(
        self, captures: Captures
    ) -> None:
        before, after = captures.before, captures.after

        assert tuple(section.name for section in before.sections) == SECTIONS
        assert before == captures.reread
        assert before.content_digest != after.content_digest
        assert before.header.receipt_cutoff == after.header.receipt_cutoff
        assert before.header.event_cutoff < after.header.event_cutoff
        assert len(before.rows('outputs')) == 260
        assert len(before.rows('receipts')) == 151
        assert before.rows('receipts') == after.rows('receipts')


class TestSamplingPoints:
    def test_rejects_downgrades_and_preserves_fitted_presentation_refresh(
        self, tmp_path: Path
    ) -> None:
        async def scenario(runtime: D1Runtime) -> None:
            async with runtime.client() as (ledger, transport):
                await transport.reset()
                await transport.bootstrap(configuration('synthetic-points'))
                release = make_release(family=FAMILY, cases=2, policy=POLICY)
                await publish_release(ledger, release)
                await ledger.activate(
                    Context(command_id='initial', principal='admin', now=BASE_MS),
                    FAMILY,
                    0,
                    release.evaluation.id,
                    release.publication.id,
                    release.points.id,
                )
                await admit_actor(ledger, 'owner', BASE_MS)
                await ledger.start_session(
                    Session(
                        id='points', actor_id='owner', family=FAMILY, created_at=BASE_MS
                    )
                )
                await accept_case(
                    ledger,
                    actor='owner',
                    session='points',
                    index=0,
                    ordinal=0,
                    choice='a',
                )
                early_header = await ledger.freeze('early', FAMILY, BASE_MS + 100)
                early = analyze_export(
                    await capture_export(ledger, early_header)
                ).points
                await ledger.stage_points(early, BASE_MS + 101)
                await accept_case(
                    ledger,
                    actor='owner',
                    session='points',
                    index=1,
                    ordinal=1,
                    choice='b',
                )
                header = await ledger.freeze('current', FAMILY, BASE_MS + 102)
                fitted = analyze_export(await capture_export(ledger, header)).points
                await ledger.stage_points(fitted, BASE_MS + 103)

                current = await ledger.activate(
                    Context(command_id='fit', principal='admin', now=BASE_MS + 104),
                    FAMILY,
                    header.family_revision,
                    release.evaluation.id,
                    release.publication.id,
                    fitted.id,
                )

                assert await ledger.freshness('current') == 'current'
                assert await ledger.freshness('early') == 'stale'
                status = await LedgerInvestigation(transport).status(FAMILY)
                assert status is not None and status.sampling_source is not None
                assert status.state.point_id == fitted.id
                assert status.sampling_source.model_dump() == {
                    'export_id': header.id,
                    'analysis_sequence': header.analysis_sequence,
                    'event_cutoff': header.event_cutoff,
                    'receipt_cutoff': header.receipt_cutoff,
                }

                for name, points in (('initial', release.points), ('earlier', early)):
                    with pytest.raises(CommandRejected, match='point_downgrade'):
                        await ledger.activate(
                            Context(
                                command_id=f'downgrade:{name}',
                                principal='admin',
                                now=BASE_MS + 105,
                            ),
                            FAMILY,
                            current.seq,
                            release.evaluation.id,
                            release.publication.id,
                            points.id,
                        )

                refresh = make_release(
                    family=FAMILY, cases=2, policy=POLICY, suffix='refreshed'
                )
                await publish_presentation(ledger, refresh)

                await ledger.activate(
                    Context(command_id='refresh', principal='admin', now=BASE_MS + 106),
                    FAMILY,
                    current.seq,
                    release.evaluation.id,
                    refresh.publication.id,
                    fitted.id,
                )

                refreshed = await ledger.freeze('refreshed', FAMILY, BASE_MS + 107)
                assert refreshed.evaluation_id == header.evaluation_id
                assert refreshed.publication_id == refresh.publication.id
                assert refreshed.point_id == fitted.id
                assert await ledger.freshness('current') == 'current'

        with D1Runtime(tmp_path).owner() as runtime:
            asyncio.run(scenario(runtime))


class TestAnalyzeExport:
    @pytest.mark.parametrize(
        'change', ['missing', 'case-presentation', 'output-content']
    )
    def test_rejects_a_rehashed_capture_with_missing_or_stale_public_approval(
        self, captures: Captures, change: str
    ) -> None:
        publication = Publication.model_validate_json(
            canonical(captures.before.rows('publication')[0])
        )
        approval = publication.approval
        assert approval is not None
        match change:
            case 'missing':
                approval = None
            case 'case-presentation':
                case_approval, *remaining_cases = approval.cases
                approval = approval.model_copy(
                    update={
                        'cases': (
                            case_approval.model_copy(
                                update={'presentation_digest': '0' * 64}
                            ),
                            *remaining_cases,
                        )
                    }
                )
            case 'output-content':
                output_approval, *remaining_outputs = approval.outputs
                approval = approval.model_copy(
                    update={
                        'outputs': (
                            output_approval.model_copy(
                                update={'content_digest': '0' * 64}
                            ),
                            *remaining_outputs,
                        )
                    }
                )
            case _:
                raise AssertionError(change)
        invalid = publication.model_copy(update={'approval': approval})
        sections = tuple(
            section.model_copy(update={'rows': (canonical(invalid),)})
            if section.name == 'publication'
            else section
            for section in captures.before.sections
        )
        payload: dict[str, JsonValue] = {
            'header': captures.before.header.model_dump(mode='json'),
            'sections': [section.model_dump(mode='json') for section in sections],
        }
        capture = FrozenExport.model_validate_json(
            canonical(
                {
                    **payload,
                    'content_digest': digest(payload),
                }
            )
        )

        with pytest.raises(ValueError):
            analyze_export(capture)

    def test_rejects_a_sealed_policy_other_than_the_fixed_one(
        self, captures: Captures, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rules = POLICY.rules.model_copy(update={'rater_agreement_floor': 0.8})
        monkeypatch.setattr(
            snapshots, 'POLICY', POLICY.model_copy(update={'rules': rules})
        )

        with pytest.raises(ValueError, match='unsupported analysis policy'):
            analyze_export(captures.before)

    def test_matches_the_committed_genuine_capped_report(
        self,
        captures: Captures,
        stale_fixture: Callable[[Path, str], None],
    ) -> None:
        snapshot = analyze_export(captures.before)

        closed = close(snapshot)

        written = closed.model_dump_json(indent=2) + '\n'
        if not REPORT_FIXTURE.exists():
            stale_fixture(REPORT_FIXTURE, written)
            return

        recorded = REPORT_FIXTURE.read_text()
        expected = report.Report.model_validate_json(recorded)
        assert isinstance(closed.results, report.VotesResults)
        assert isinstance(expected.results, report.VotesResults)
        assert isinstance(closed.results.provenance, LedgerProvenance)
        assert isinstance(expected.results.provenance, LedgerProvenance)
        assert snapshot.points.id == digest(
            snapshot.points.model_dump(mode='json', exclude={'id'})
        )
        assert closed.results.provenance.fitted_point_snapshot_id == snapshot.points.id
        assert {row.system: row.score for row in closed.results.standings} == {
            system: point.score
            for system, point in snapshot.points.points.items()
            if point.score is not None
        }
        assert [row.pass_rate for row in closed.results.standings] == [None] * len(
            closed.results.standings
        )

        scores = {row.system: row.score for row in expected.results.standings}
        expected_points = point_snapshot(
            snapshot.points.evaluation_id,
            snapshot.points.policy_id,
            {
                system: Point(score=scores.get(system), component=point.component)
                for system, point in snapshot.points.points.items()
            },
            source_export_id=snapshot.points.source_export_id,
            source_export_digest=snapshot.points.source_export_digest,
            weighted_evidence_digest=snapshot.points.weighted_evidence_digest,
        )
        if expected.results.provenance.fitted_point_snapshot_id != expected_points.id:
            stale_fixture(REPORT_FIXTURE, written)
            return

        comparison = json.loads(recorded)
        # BLAS implementations can move the fitted score by about 2e-11 points.
        for row in comparison['results']['standings']:
            row['score'] = pytest.approx(row['score'], rel=0, abs=1e-9)
        comparison['results']['position'] = pytest.approx(
            expected.results.position, rel=0, abs=1e-9
        )
        comparison['results']['provenance']['fitted_point_snapshot_id'] = (
            snapshot.points.id
        )
        if comparison != json.loads(written):
            stale_fixture(REPORT_FIXTURE, written)

    def test_caps_the_complete_actor_across_sessions_and_drops_repeats(
        self, captures: Captures
    ) -> None:
        snapshot = analyze_export(captures.before)
        analysis = snapshot.analysis
        masses: defaultdict[str | None, float] = defaultdict(float)
        for row in analysis.ordinary:
            masses[row.actor] += row.comparison.weight

        assert masses == pytest.approx({'owner': 100.0, 'visitor': 20.0})
        assert len(analysis.ordinary) == 150
        assert analysis.accepted == 151
        assert snapshot.points.source_export_digest == captures.before.content_digest
        assert (
            snapshot.points.weighted_evidence_digest
            == analysis.provenance.weighted_evidence_digest
        )

    def test_recomputes_the_next_denominator_without_revising_the_old_capture(
        self, captures: Captures
    ) -> None:
        before = analyze_export(captures.before).analysis
        after = analyze_export(captures.after).analysis
        owner_before = [row for row in before.ordinary if row.actor == 'owner']
        owner_after = [row for row in after.ordinary if row.actor == 'owner']

        assert [row.comparison.weight for row in owner_before] == pytest.approx(
            [100 / 130] * 130
        )
        assert [row.comparison.weight for row in owner_after] == pytest.approx(
            [100 / 129] * 129
        )
        assert after.accepted == before.accepted == 151
        results = report.votes_results(after)
        assert results.votes == 149
        assert results.dropped_votes == 2
        assert results.effective_votes == pytest.approx(120)

    def test_closes_with_the_owner_stopped_and_current_inputs_absent(
        self, captures: Captures, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse_socket(*args: object, **kwargs: object) -> None:
            raise AssertionError('Offline report attempted network access.')

        monkeypatch.setattr(socket, 'socket', refuse_socket)
        monkeypatch.setattr(families, 'FAMILIES_DIR', tmp_path / 'missing-families')
        monkeypatch.setenv('BENCH_NIXOS_CONFIG', str(tmp_path / 'missing-catalog.json'))
        path = tmp_path / 'capture.json'
        write_export(captures.before, path)

        closed = close(analyze_export(read_export(path)))

        assert closed.results.scorer == 'votes'
        assert closed.results.votes == 150
        assert closed.results.dropped_votes == 1
        assert closed.results.effective_votes == pytest.approx(120)
        assert set(closed.systems) == {'large', 'small'}

        directory = tmp_path / 'reports'
        monkeypatch.setattr(report, 'REPORTS_DIR', directory)

        result = CliRunner().invoke(cli.app, ['close', FAMILY, '--snapshot', str(path)])
        assert result.exit_code == 0, result.output

        (saved,) = (directory / FAMILY).iterdir()
        parsed = report.Report.model_validate_json(saved.read_text())
        assert parsed.results == closed.results
        assert parsed.systems == closed.systems


class TestReadExport:
    @pytest.mark.parametrize(
        'family', ['../outside', '/outside', 'family\n', 'Uppercase']
    )
    def test_rejects_unsafe_frozen_family_names(
        self, captures: Captures, family: str
    ) -> None:
        payload = captures.before.model_dump(mode='json')
        payload['header']['family'] = family

        with pytest.raises(ValidationError):
            FrozenExport.model_validate(payload)

    @pytest.mark.parametrize(
        'change',
        [
            'missing-section',
            'duplicate-section',
            'order',
            'duplicate-row',
            'noncanonical-row',
            'content-digest',
            'truncated-history',
        ],
    )
    def test_rejects_incomplete_or_corrupted_saved_capture(
        self, captures: Captures, tmp_path: Path, change: str
    ) -> None:
        payload = captures.before.model_dump(mode='json')
        sections = payload['sections']
        match change:
            case 'missing-section':
                sections.pop()
            case 'duplicate-section':
                sections.append(sections[0])
            case 'order':
                sections.reverse()
            case 'duplicate-row':
                sections[0]['rows'].append(sections[0]['rows'][0])
            case 'noncanonical-row':
                sections[0]['rows'][0] = json.dumps(
                    json.loads(sections[0]['rows'][0]), indent=2
                )
            case 'content-digest':
                payload['header']['created_at'] += 1
            case 'truncated-history':
                receipts = next(row for row in sections if row['name'] == 'receipts')
                removed = json.loads(receipts['rows'].pop())['seq']
                for section in sections:
                    if section['name'] in ('events', 'targets'):
                        field = 'seq' if section['name'] == 'events' else 'event_seq'
                        section['rows'] = [
                            row
                            for row in section['rows']
                            if json.loads(row)[field] != removed
                        ]
            case _:
                raise AssertionError(change)

        if change != 'content-digest':
            payload['content_digest'] = digest(
                {
                    key: value
                    for key, value in payload.items()
                    if key != 'content_digest'
                }
            )
        path = tmp_path / 'corrupted.json'
        path.write_text(canonical(payload))

        with pytest.raises(ValidationError):
            read_export(path)

    def test_creates_a_private_capture_and_preserves_an_existing_artifact(
        self, captures: Captures, tmp_path: Path
    ) -> None:
        path = tmp_path / 'review/capture.json'

        write_export(captures.before, path)
        loaded = read_export(path)

        assert loaded == captures.before
        assert S_IMODE(path.stat().st_mode) == 0o600
        before = path.read_bytes()
        with pytest.raises(FileExistsError):
            write_export(captures.after, path)
        assert path.read_bytes() == before
