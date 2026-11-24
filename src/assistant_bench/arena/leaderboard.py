"""Capture, export, and confirm delayed public arena standings."""

import argparse
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Json, TypeAdapter
from sqlalchemy import and_, delete, exists, func, literal, or_, select, tuple_, update
from sqlalchemy.dialects import sqlite
from sqlalchemy.orm import aliased

from assistant_bench import report, schema
from assistant_bench.arena import d1, settlement
from assistant_bench.arena.publication import canonical_digest, timestamp_ms
from assistant_bench.database import Database, execute, fetch_all
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
    payload = None if closed is None else closed.model_dump_json()
    facts = (
        None
        if closed is None
        else closed.model_dump(mode='json', exclude={'round', 'closed_at'})
    )
    digest = canonical_digest(facts, ensure_ascii=True)

    candidates = schema.LeaderboardCandidates
    hour_slot = timestamp_ms(_hour(started_at))
    captured = timestamp_ms(captured_at)
    cutoffs_json = None if cutoffs is None else cutoffs.model_dump_json()

    prior = (
        select(candidates.hour_slot, candidates.digest)
        .where(candidates.family == family)
        .order_by(candidates.hour_slot.desc())
        .limit(1)
        .cte('prior')
    )
    advanced = exists(
        select(literal(1))
        .select_from(prior)
        .where(prior.c.hour_slot < hour_slot, prior.c.digest != digest)
    )
    condition = (
        or_(~exists(select(literal(1)).select_from(prior)), advanced)
        if payload is not None or has_archive
        else advanced
    )
    source = select(
        literal(family),
        literal(hour_slot),
        literal(captured),
        literal(digest),
        literal(payload),
        literal(cutoffs_json),
    ).where(condition)
    execute(
        database,
        sqlite.insert(candidates).from_select(
            [
                candidates.family,
                candidates.hour_slot,
                candidates.captured_at,
                candidates.digest,
                candidates.report_json,
                candidates.cutoffs_json,
            ],
            source,
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
    deadline = timestamp_ms(now - EMBARGO)
    candidates = schema.LeaderboardCandidates
    family_values = func.json_each(json.dumps(list(families))).table_valued('value')
    ready = (
        select(candidates.family, func.max(candidates.hour_slot).label('hour_slot'))
        .where(
            or_(
                candidates.published_at.is_not(None), candidates.captured_at <= deadline
            ),
            candidates.family.in_(select(family_values.c.value)),
        )
        .group_by(candidates.family)
        .cte('ready')
    )
    stmt = (
        select(
            candidates.family,
            candidates.hour_slot,
            candidates.report_json.label('report'),
            candidates.published_at,
        )
        .join(
            ready,
            and_(
                candidates.family == ready.c.family,
                candidates.hour_slot == ready.c.hour_slot,
            ),
        )
        .order_by(candidates.family)
    )
    standings: Standings = {}
    manifest: Manifest = {}
    for candidate in fetch_all(database, stmt, _Candidate):
        if candidate.report is not None and candidate.report.family != candidate.family:
            raise ValueError('The candidate report names another family.')
        standings[candidate.family] = candidate.report
        if candidate.published_at is None:
            manifest[candidate.family] = candidate.hour_slot

    write_atomically(output, _STANDINGS.dump_json(standings).decode() + '\n')
    write_atomically(manifest_path, _MANIFEST.dump_json(manifest).decode() + '\n')
    return bool(manifest)


def mark(database: Database, manifest: Mapping[str, int], now: datetime) -> None:
    if not manifest:
        return

    candidates = schema.LeaderboardCandidates
    entries = func.json_each(json.dumps(dict(manifest))).table_valued('key', 'value')
    newer = aliased(candidates, name='newer')
    execute(
        database,
        update(candidates)
        .values(published_at=timestamp_ms(now))
        .where(
            tuple_(candidates.family, candidates.hour_slot).in_(
                select(entries.c.key, entries.c.value)
            ),
            candidates.captured_at <= timestamp_ms(now - EMBARGO),
            candidates.published_at.is_(None),
            ~exists(
                select(literal(1)).where(
                    newer.family == candidates.family,
                    newer.published_at.is_not(None),
                    newer.hour_slot > candidates.hour_slot,
                )
            ),
        ),
    )

    deployed = aliased(candidates, name='deployed')
    execute(
        database,
        delete(candidates).where(
            exists(
                select(literal(1)).where(
                    deployed.family == candidates.family,
                    deployed.published_at.is_not(None),
                    deployed.hour_slot > candidates.hour_slot,
                    or_(
                        candidates.published_at.is_(None),
                        func.date(deployed.hour_slot // 1000, 'unixepoch')
                        == func.date(candidates.hour_slot // 1000, 'unixepoch'),
                    ),
                )
            )
        ),
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
