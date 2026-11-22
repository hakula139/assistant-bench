import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from assistant_bench.arena import d1, leaderboard, publication
from assistant_bench.database import D1Error, SQLiteDatabase
from assistant_bench.report import Report
from support import (
    ARENA_NAMES,
    ARENA_SYSTEMS,
    arena_database,
    make_case,
    make_trial,
    make_votes_family,
    record_vote,
)


NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)


@pytest.fixture
def closed_report() -> Report:
    path = Path(__file__).parents[1] / 'fixtures' / 'report.json'
    return Report.model_validate_json(path.read_bytes())


class TestCapture:
    def test_scores_official_stores_without_sealing_or_exporting_evidence(
        self, private_store: SQLiteDatabase, arena_store: SQLiteDatabase, tmp_path: Path
    ) -> None:
        fixture = make_votes_family()
        family = fixture.model_copy(
            update={'settings': fixture.settings.model_copy(update={'public': True})}
        )
        prepared_at = NOW - timedelta(days=1)
        public_case, held_case = make_case('public'), make_case('held', 'held-out')
        cases = [public_case, held_case]
        trials = [
            make_trial(case.id, system) for case in cases for system in ARENA_SYSTEMS
        ]
        production = publication.prepare(
            family, cases, trials, ARENA_NAMES, target='production'
        )
        private = publication.prepare(
            family, cases, trials, ARENA_NAMES, target='private'
        )

        for database, published in (
            (arena_store, production),
            (private_store, private),
        ):
            publication.stage(database, published, prepared_at)
            publication.activate(database, published.id, prepared_at)
            record_vote(
                database,
                published,
                NOW,
                actor=f'{published.target}-identity',
                age=timedelta(hours=1),
            )

        with arena_database() as staging:
            publication.stage(staging, production, prepared_at)
            publication.activate(staging, production.id, prepared_at)
            record_vote(
                staging, production, NOW, actor='staging-only', age=timedelta(hours=1)
            )
            leaderboard.capture(
                private_store, arena_store, [family], NOW + timedelta(minutes=4)
            )

        current, manifest = tmp_path / 'current.json', tmp_path / 'manifest.json'
        assert leaderboard.export(
            private_store,
            [family.name],
            current,
            manifest,
            datetime.now(UTC) + timedelta(hours=3),
        )
        data = json.loads(current.read_bytes())[family.name]
        closed = Report.model_validate(data)
        assert closed.closed_at == NOW
        result = closed.results
        assert result.scorer == 'votes'
        assert result.votes == result.effective_votes == 2
        assert result.voters == 2
        assert result.cutoffs is None
        assert private_store.execute('SELECT id FROM vote_snapshots').rows == []
        (row,) = private_store.execute(
            'SELECT cutoffs_json FROM leaderboard_candidates'
        ).rows
        assert isinstance(row['cutoffs_json'], str)
        assert json.loads(row['cutoffs_json']) == {
            'production': {'id': 1, 'votes': 1},
            'private': {'id': 1, 'votes': 1},
        }
        assert all(
            value not in current.read_text()
            for value in (
                production.id,
                private.id,
                'private-identity',
                'production-identity',
                'staging-only',
                held_case.instruction,
            )
        )


class TestStage:
    def test_keeps_first_capture_and_deduplicates_only_consecutive_payloads(
        self, private_store: SQLiteDatabase, closed_report: Report
    ) -> None:
        same = closed_report.model_copy(update={'closed_at': NOW + timedelta(hours=1)})
        changed = closed_report.model_copy(
            update={
                'results': closed_report.results.model_copy(update={'dropped_votes': 1})
            }
        )
        family = closed_report.family
        leaderboard.stage(private_store, family, None, NOW, NOW)
        stages = [
            (0, closed_report),
            (0, changed),
            (1, same),
            (2, changed),
            (3, closed_report),
            (2, None),
        ]
        for offset, value in stages:
            hour = NOW + timedelta(hours=offset)
            leaderboard.stage(private_store, family, value, hour, hour)

        rows = private_store.execute(
            'SELECT hour_slot, report_json FROM leaderboard_candidates '
            'ORDER BY hour_slot'
        ).rows
        assert rows == [
            {
                'hour_slot': int((NOW + timedelta(hours=offset)).timestamp() * 1000),
                'report_json': value.model_dump_json(),
            }
            for offset, value in [(0, closed_report), (2, changed), (3, closed_report)]
        ]


class TestExport:
    @pytest.mark.parametrize('empty', [False, True])
    def test_observes_capture_time_and_preserves_dated_reports(
        self,
        private_store: SQLiteDatabase,
        closed_report: Report,
        reports_dir: Path,
        tmp_path: Path,
        empty: bool,
    ) -> None:
        family = closed_report.family
        historic = reports_dir / family / f'{closed_report.round}.json'
        historic.parent.mkdir(parents=True)
        historic.write_text(closed_report.model_dump_json())
        capture_end = NOW + timedelta(minutes=17)
        cutoff = capture_end + timedelta(hours=2)
        candidate = None if empty else closed_report
        leaderboard.stage(
            private_store, family, candidate, NOW, capture_end, has_archive=True
        )
        current, manifest = tmp_path / 'current.json', tmp_path / 'manifest.json'

        assert not leaderboard.export(
            private_store,
            [family],
            current,
            manifest,
            cutoff - timedelta(milliseconds=1),
        )
        assert (
            json.loads(current.read_bytes()) == json.loads(manifest.read_bytes()) == {}
        )
        assert not leaderboard.export(
            private_store, ['other'], current, manifest, cutoff
        )

        assert leaderboard.export(private_store, [family], current, manifest, cutoff)
        expected = None if candidate is None else candidate.model_dump(mode='json')
        assert json.loads(current.read_bytes()) == {family: expected}
        assert historic.read_text() == closed_report.model_dump_json()


class TestMark:
    def test_retries_and_cannot_replace_a_newer_confirmation(
        self, private_store: SQLiteDatabase, closed_report: Report, tmp_path: Path
    ) -> None:
        family = closed_report.family
        current, manifest = tmp_path / 'current.json', tmp_path / 'manifest.json'
        changed = closed_report.model_copy(
            update={
                'results': closed_report.results.model_copy(update={'dropped_votes': 1})
            }
        )
        earlier_day = NOW - timedelta(days=1)
        leaderboard.stage(private_store, family, changed, earlier_day, earlier_day)
        assert leaderboard.export(
            private_store, [family], current, manifest, earlier_day + timedelta(hours=2)
        )
        dated = json.loads(manifest.read_bytes())
        leaderboard.mark(private_store, dated, earlier_day + timedelta(hours=2))

        middle, newer = NOW + timedelta(hours=1), NOW + timedelta(hours=2)
        leaderboard.stage(private_store, family, closed_report, NOW, NOW)
        leaderboard.stage(private_store, family, changed, middle, middle)
        leaderboard.stage(private_store, family, None, newer, newer)
        assert leaderboard.export(
            private_store, [family], current, manifest, NOW + timedelta(hours=2)
        )
        initial = json.loads(manifest.read_bytes())
        leaderboard.mark(private_store, initial, NOW + timedelta(hours=2))
        assert not leaderboard.export(
            private_store, [family], current, manifest, NOW + timedelta(hours=2)
        )
        assert json.loads(current.read_bytes()) == {
            family: closed_report.model_dump(mode='json')
        }
        assert leaderboard.export(
            private_store, [family], current, manifest, middle + timedelta(hours=2)
        )
        older = json.loads(manifest.read_bytes())

        assert leaderboard.export(
            private_store, [family], current, manifest, newer + timedelta(hours=2)
        )
        selected = json.loads(manifest.read_bytes())
        assert json.loads(current.read_bytes()) == {family: None}
        leaderboard.mark(private_store, selected, newer + timedelta(hours=1))
        assert leaderboard.export(
            private_store, [family], current, manifest, newer + timedelta(hours=2)
        )

        for receipt in (selected, older, selected):
            leaderboard.mark(private_store, receipt, newer + timedelta(hours=2))
        assert not leaderboard.export(
            private_store, [family], current, manifest, newer + timedelta(hours=2)
        )
        assert json.loads(current.read_bytes()) == {family: None}
        assert json.loads(manifest.read_bytes()) == {}
        assert private_store.execute(
            'SELECT hour_slot FROM leaderboard_candidates ORDER BY hour_slot'
        ).rows == [{'hour_slot': dated[family]}, {'hour_slot': selected[family]}]


class TestMain:
    def test_keeps_private_errors_out_of_public_logs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def unavailable(_: str) -> None:
            raise D1Error('Private SQL reply with details.')

        monkeypatch.setattr('sys.argv', ['leaderboard', 'capture'])
        monkeypatch.setattr(d1, 'connect', unavailable)

        with pytest.raises(
            SystemExit, match=r'^Arena leaderboard publication failed\.$'
        ):
            leaderboard.main()
