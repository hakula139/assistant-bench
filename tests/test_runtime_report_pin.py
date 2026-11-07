from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock

import pytest

from assistant_bench import canaries, cli, report
from assistant_bench.catalog import NixosConfigExport, System
from assistant_bench.families import load_family
from assistant_bench.harnesses import fingerprint
from assistant_bench.messages import InputError
from assistant_bench.runtime import RunContext
from support import REPORT_FIXTURE, make_system, write_votes_family


class TestRun:
    @pytest.mark.usefixtures('export')
    @pytest.mark.parametrize(
        ('pinned_matches', 'live_exists'),
        [(True, True), (True, False), (False, True), (False, False)],
        ids=[
            'pinned-matches-live-edited',
            'pinned-matches-live-deleted',
            'pinned-mismatch-live-edited',
            'pinned-mismatch-live-deleted',
        ],
    )
    def test_uses_pinned_reports_after_live_history_changes(
        self,
        families_dir: Path,
        run_context: RunContext,
        monkeypatch: pytest.MonkeyPatch,
        pinned_matches: bool,
        live_exists: bool,
    ) -> None:
        system = make_system()
        current = fingerprint(system)
        write_votes_family(families_dir, [system.name], {'greeting': 'message'})
        family = load_family('f')
        closed = report.Report.model_validate_json(REPORT_FIXTURE.read_text())
        metadata = closed.systems[system.name]
        pinned_fingerprint = current if pinned_matches else 'fp-pinned'
        closed = closed.model_copy(
            update={
                'family': family.name,
                'systems': closed.systems
                | {
                    system.name: metadata.model_copy(
                        update={'fingerprint': pinned_fingerprint}
                    )
                },
            }
        )
        live = report.write_report(closed)
        pinned = run_context.public_root / 'reports' / family.name / live.name
        pinned.parent.mkdir(parents=True)
        pinned.write_bytes(live.read_bytes())
        live_fingerprint = 'fp-live' if pinned_matches else current
        if live_exists:
            report.write_report(
                closed.model_copy(
                    update={
                        'systems': closed.systems
                        | {
                            system.name: metadata.model_copy(
                                update={'fingerprint': live_fingerprint}
                            )
                        }
                    }
                )
            )
        else:
            live.unlink()
        monkeypatch.setattr(cli, 'open_context', lambda: nullcontext(run_context))

        def load_systems(
            export: NixosConfigExport, *, root: Path | None = None
        ) -> dict[str, System]:
            return {system.name: system}

        monkeypatch.setattr(cli, 'load_systems', load_systems)
        prepare = Mock(side_effect=RuntimeError('Reached canary preparation.'))
        monkeypatch.setattr(canaries, 'prepare', prepare)

        if pinned_matches:
            with pytest.raises(RuntimeError, match='Reached canary preparation'):
                cli.run([family.name])
        else:
            with pytest.raises(InputError, match='`fp-pinned`'):
                cli.run([family.name])

        assert prepare.call_count == int(pinned_matches)
        assert report.reported_fingerprints(family.name) == (
            {name: item.fingerprint for name, item in closed.systems.items()}
            | {system.name: live_fingerprint}
            if live_exists
            else {}
        )
