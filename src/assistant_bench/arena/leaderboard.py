"""Capture, export, and confirm delayed public arena standings."""

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Json, TypeAdapter

from assistant_bench import report
from assistant_bench.arena import d1, settlement
from assistant_bench.database import Database
from assistant_bench.families import (
    Family,
    FamilyName,
    VotesSettings,
    family_names,
    load_family,
)
from assistant_bench.paths import REPORTS_DIR, write_atomically


EMBARGO = timedelta(hours=2)

type Manifest = dict[FamilyName, int]
type Standings = dict[FamilyName, report.Report | None]

_MANIFEST = TypeAdapter(Manifest)
_STANDINGS = TypeAdapter(Standings)


class _Candidate(BaseModel):
    model_config = ConfigDict(extra='forbid')

    family: FamilyName
    hour_slot: int
    report: Json[report.Report] | None
    published_at: int | None


def _timestamp(time: datetime) -> int:
    return int(time.timestamp() * 1000)


def _hour(time: datetime) -> datetime:
    return time.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def _public_families() -> list[Family]:
    return [
        family
        for name in family_names()
        if (family := load_family(name)).settings.public
        and isinstance(family.settings, VotesSettings)
    ]


# ── Capture ──


def capture(
    private: Database,
    production: Database,
    families: Sequence[Family],
    started_at: datetime,
) -> None:
    """Read the official stores and stage one changed aggregate per family per hour."""
    for family in families:
        snapshot = settlement.capture(
            family, private, production, started_at, revision=None
        )
        closed = None
        cutoffs = None
        if snapshot is not None:
            evidence = settlement.analysis(private, snapshot)
            cutoffs = evidence.cutoffs
            if evidence.ordinary or evidence.settlements:
                public_snapshot = snapshot.model_copy(
                    update={'closed_at': _hour(started_at)}
                )
                closed = settlement.score(public_snapshot, evidence)
        stage(
            private,
            family.name,
            closed,
            started_at,
            datetime.now(UTC),
            cutoffs=cutoffs,
            has_archive=closed is None
            and any((REPORTS_DIR / family.name).glob('*.json')),
        )


def stage(
    database: Database,
    family: str,
    closed: report.Report | None,
    started_at: datetime,
    captured_at: datetime,
    *,
    cutoffs: report.Cutoffs | None = None,
    has_archive: bool = False,
) -> None:
    """Stage a completed cut if this hour and content advance the family."""
    payload = None if closed is None else closed.model_dump_json()
    facts = (
        None
        if closed is None
        else closed.model_dump(mode='json', exclude={'round', 'closed_at'})
    )
    canonical = json.dumps(facts, sort_keys=True, separators=(',', ':'))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    database.execute(
        """WITH prior AS (
               SELECT hour_slot, digest FROM leaderboard_candidates
               WHERE family = ?1 ORDER BY hour_slot DESC LIMIT 1
           )
           INSERT INTO leaderboard_candidates (
               family, hour_slot, captured_at, digest, report_json, cutoffs_json
           )
           SELECT ?1, ?2, ?3, ?4, ?5, ?6
           WHERE ((?5 IS NOT NULL OR ?7) AND NOT EXISTS (SELECT 1 FROM prior))
               OR EXISTS (SELECT 1 FROM prior WHERE hour_slot < ?2 AND digest <> ?4)""",
        (
            family,
            _timestamp(_hour(started_at)),
            _timestamp(captured_at),
            digest,
            payload,
            None if cutoffs is None else cutoffs.model_dump_json(),
            int(has_archive),
        ),
    )


# ── Release ──


def export(
    database: Database,
    families: Sequence[str],
    output: Path,
    manifest_path: Path,
    now: datetime,
) -> bool:
    """Write the build overlay and its private deployment manifest.

    Return whether the overlay contains an unconfirmed, matured candidate.
    """
    deadline = _timestamp(now - EMBARGO)
    rows = database.execute(
        """WITH ready AS (
               SELECT family, max(hour_slot) AS hour_slot
               FROM leaderboard_candidates
               WHERE (published_at IS NOT NULL OR captured_at <= ?)
                   AND family IN (SELECT value FROM json_each(?))
               GROUP BY family
           )
           SELECT family, hour_slot, report_json AS report, published_at
           FROM leaderboard_candidates JOIN ready USING (family, hour_slot)
           ORDER BY family""",
        (deadline, json.dumps(families)),
    ).rows
    standings: Standings = {}
    manifest: Manifest = {}
    for row in rows:
        candidate = _Candidate.model_validate(row)
        if candidate.report is not None and candidate.report.family != candidate.family:
            raise ValueError('The candidate report names another family.')
        standings[candidate.family] = candidate.report
        if candidate.published_at is None:
            manifest[candidate.family] = candidate.hour_slot

    write_atomically(output, _STANDINGS.dump_json(standings).decode() + '\n')
    write_atomically(manifest_path, _MANIFEST.dump_json(manifest).decode() + '\n')
    return bool(manifest)


def mark(database: Database, manifest: Mapping[str, int], now: datetime) -> None:
    """Confirm reported deployment selections without regressing their release floor."""
    if not manifest:
        return

    database.execute(
        """UPDATE leaderboard_candidates AS candidate SET published_at = ?
           WHERE (family, hour_slot) IN (SELECT key, value FROM json_each(?))
               AND captured_at <= ? AND published_at IS NULL
               AND NOT EXISTS (
                   SELECT 1 FROM leaderboard_candidates AS newer
                   WHERE newer.family = candidate.family
                       AND newer.published_at IS NOT NULL
                       AND newer.hour_slot > candidate.hour_slot
               )""",
        (_timestamp(now), json.dumps(dict(manifest)), _timestamp(now - EMBARGO)),
    )
    database.execute(
        """DELETE FROM leaderboard_candidates AS older
           WHERE EXISTS (
               SELECT 1 FROM leaderboard_candidates AS deployed
               WHERE deployed.family = older.family
                   AND deployed.published_at IS NOT NULL
                   AND deployed.hour_slot > older.hour_slot
                   AND (
                       older.published_at IS NULL
                       OR date(deployed.hour_slot / 1000, 'unixepoch')
                           = date(older.hour_slot / 1000, 'unixepoch')
                   )
           )"""
    )


# ── Command ──


def main() -> None:
    """Run one protected publication step with a public-log-safe failure message."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('capture')
    export_parser = commands.add_parser('export')
    export_parser.add_argument('output', type=Path)
    export_parser.add_argument('manifest', type=Path)
    commands.add_parser('mark').add_argument('manifest', type=Path)
    args = parser.parse_args()

    try:
        with d1.connect('private') as database:
            match args.command:
                case 'capture':
                    families = _public_families()
                    with d1.connect('production') as production:
                        capture(database, production, families, datetime.now(UTC))
                case 'export':
                    changed = export(
                        database,
                        [family.name for family in _public_families()],
                        args.output,
                        args.manifest,
                        datetime.now(UTC),
                    )
                    print('true' if changed else 'false')
                case 'mark':
                    manifest = _MANIFEST.validate_json(args.manifest.read_bytes())
                    mark(database, manifest, datetime.now(UTC))
                case _:
                    parser.error('Unknown leaderboard command.')
    except Exception:
        raise SystemExit('Arena leaderboard publication failed.') from None


if __name__ == '__main__':
    main()
