import asyncio
from collections.abc import Iterator

import pytest

from arena_support import native_management, vote_capture
from assistant_bench.arena.publication import POLICY
from assistant_bench.arena.publishing import (
    publish_points,
    publish_release,
)
from assistant_bench.arena.snapshots import analyze_export
from assistant_bench_arena.management_domain import PRINCIPAL
from ledger_fixtures import make_release
from ledger_support import D1Runtime


@pytest.fixture(scope='module')
def runtime(tmp_path_factory: pytest.TempPathFactory) -> Iterator[D1Runtime]:
    with D1Runtime(tmp_path_factory.mktemp('native-publication')).owner() as owner:
        yield owner


class TestPublishRelease:
    @pytest.mark.parametrize('lost', ['stage_output', 'activate'])
    def test_retries_a_committed_operation_without_changing_the_reviewed_release(
        self, runtime: D1Runtime, lost: str
    ) -> None:
        async def scenario() -> None:
            async with native_management(runtime) as management:
                release = make_release(cases=2)
                management.lose_reply = lost

                with pytest.raises(ValueError, match='response was lost'):
                    await publish_release(management, release)

                before = await management.ledger.read.family('writing')
                assert (before is not None) == (lost == 'activate')
                management.now += 100
                await publish_release(management, release)
                current = await management.ledger.read.family('writing')

                assert current is not None
                assert current.evaluation_id == release.evaluation.id
                assert current.publication_id == release.publication.id
                assert current.point_id == release.points.id
                operations = len(management.operations)
                assert await publish_release(management, release) is None
                assert len(management.operations) == operations + 1
                assert await management.ledger.read.family('writing') == current

                if before is not None:
                    assert current == before

        asyncio.run(scenario())

    def test_preserves_genuinely_fitted_points_through_presentation_refresh(
        self, runtime: D1Runtime
    ) -> None:
        async def scenario() -> None:
            async with native_management(runtime) as management:
                release = make_release(cases=2, policy=POLICY)
                await publish_release(management, release)
                capture = await vote_capture(management, release)
                fitted = analyze_export(capture).points
                management.lose_reply = 'activate'

                with pytest.raises(ValueError, match='response was lost'):
                    await publish_points(management, 'writing', fitted)

                assert await publish_points(management, 'writing', fitted) is None
                refreshed = make_release(
                    cases=2,
                    policy=release.policy,
                    suffix='approved-label',
                )
                assert refreshed.evaluation.id == release.evaluation.id
                assert refreshed.publication.id != release.publication.id

                event = await publish_release(management, refreshed)
                current = await management.ledger.read.family('writing')

                assert event is not None and event.principal == PRINCIPAL
                assert current is not None
                assert current.publication_id == refreshed.publication.id
                assert current.point_id == fitted.id
                assert await management.ledger.freshness(capture.header.id) == 'current'
                status = await management.ledger.investigation.status('writing')
                assert status is not None and status.sampling_source is not None
                assert status.sampling_source.export_id == capture.header.id

        asyncio.run(scenario())


class TestPublishPoints:
    def test_refuses_initial_points_before_contacting_the_owner(
        self, runtime: D1Runtime
    ) -> None:
        async def scenario() -> None:
            async with native_management(runtime) as management:
                release = make_release(cases=2)

                with pytest.raises(ValueError, match='registered source export'):
                    await publish_points(management, 'writing', release.points)

                assert management.operations == []

        asyncio.run(scenario())

    def test_refuses_a_genuine_fit_after_another_evaluation_becomes_active(
        self, runtime: D1Runtime
    ) -> None:
        async def scenario() -> None:
            async with native_management(runtime) as management:
                release = make_release(cases=2, policy=POLICY)
                await publish_release(management, release)
                fitted = analyze_export(await vote_capture(management, release)).points
                replacement = make_release(cases=3, policy=release.policy)
                await publish_release(management, replacement)
                before = await management.ledger.read.family('writing')
                operations = len(management.operations)

                with pytest.raises(ValueError, match='no longer active'):
                    await publish_points(management, 'writing', fitted)

                assert [
                    row.operation for row in management.operations[operations:]
                ] == ['family_status']
                assert await management.ledger.read.family('writing') == before

        asyncio.run(scenario())
