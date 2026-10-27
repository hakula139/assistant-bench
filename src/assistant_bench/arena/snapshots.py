"""Frozen numerical evidence, independent of current files and live projections."""

from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel

from assistant_bench.arena.evidence import replay
from assistant_bench.arena.exports import FrozenExport
from assistant_bench.arena.publication import POLICY
from assistant_bench.report import (
    AnalysisInput,
    HumanEvidence,
    LedgerProvenance,
    OutputMeasurement,
)
from assistant_bench.scoring import ranking
from assistant_bench.scoring.ranking import Comparison
from assistant_bench.scoring.weighting import actor_weights
from assistant_bench_arena.ledger_models import (
    Actor,
    Case,
    Evaluation,
    Event,
    EventTarget,
    ExportSection,
    NumericalInput,
    Output,
    Point,
    PointSnapshot,
    Publication,
    Receipt,
    Session,
    System,
)
from assistant_bench_arena.releases import (
    EvaluationBundle,
    point_snapshot,
    validate_presentation,
)
from assistant_bench_arena.serialization import canonical, digest
from assistant_bench_arena.snapshots import AnalysisPolicy, Settlement


@dataclass(frozen=True)
class SnapshotAnalysis:
    analysis: AnalysisInput
    fingerprints: dict[str, str]
    system_names: dict[str, str]
    """Public display names the release recorded for its systems."""

    points: PointSnapshot


def _records[Record: BaseModel](
    capture: FrozenExport, section: ExportSection, model: type[Record]
) -> tuple[Record, ...]:
    return tuple(
        model.model_validate_json(canonical(row)) for row in capture.rows(section)
    )


def _one[Record: BaseModel](
    capture: FrozenExport, section: ExportSection, model: type[Record]
) -> Record:
    rows = _records(capture, section, model)
    if len(rows) != 1:
        raise ValueError(f'The `{section}` export section must contain one record.')

    return rows[0]


def analyze_export(capture: FrozenExport) -> SnapshotAnalysis:
    """Verify and fit a saved capture without current files or a live server."""
    header = capture.header
    policy = _one(capture, 'policy', AnalysisPolicy)
    evaluation = _one(capture, 'evaluation', Evaluation)
    publication = _one(capture, 'publication', Publication)
    sampling_points = _one(capture, 'points', PointSnapshot)
    cases = _records(capture, 'cases', Case)
    systems = _records(capture, 'systems', System)
    outputs = _records(capture, 'outputs', Output)
    numerics = _records(capture, 'numerics', NumericalInput)
    inventory = EvaluationBundle(
        policy=policy,
        evaluation=evaluation,
        cases=cases,
        systems=systems,
        outputs=outputs,
        numerics=numerics,
    )
    validate_presentation(inventory, publication)

    if (
        evaluation.id != header.evaluation_id
        or evaluation.family != header.family
        or publication.id != header.publication_id
        or publication.evaluation_id != evaluation.id
        or sampling_points.id != header.point_id
        or sampling_points.evaluation_id != evaluation.id
        or sampling_points.policy_id != header.policy_id
        or sampling_points.id
        != digest(sampling_points.model_dump(mode='json', exclude={'id'}))
        or set(sampling_points.points) != {system.id for system in systems}
        or digest(policy) != header.policy_id
        or policy != header.policy
    ):
        raise ValueError('The export registration does not bind its sealed inventory.')

    if policy != POLICY:
        raise ValueError('The export names an unsupported analysis policy.')

    observations = _records(capture, 'receipts', Receipt)
    events = _records(capture, 'events', Event)
    targets = _records(capture, 'targets', EventTarget)
    actors = _records(capture, 'actors', Actor)
    sessions = _records(capture, 'sessions', Session)
    if (
        any(
            row.seq > header.receipt_cutoff or row.family != header.family
            for row in observations
        )
        or any(
            row.seq > header.event_cutoff or row.family != header.family
            for row in events
        )
        or any(row.family != header.family for row in sessions)
    ):
        raise ValueError('Captured evidence exceeds its frozen family or cutoffs.')

    session_map = {session.id: session for session in sessions}
    if len(session_map) != len(sessions):
        raise ValueError('Captured session identities must be unique.')

    for row in observations:
        session = session_map.get(row.session_id)
        if session is None or session.actor_id != row.actor_id:
            raise ValueError('A receipt is missing its original session provenance.')

    selected = replay(actors, observations, events, targets)
    output_map = {output.key: output for output in outputs}
    fingerprints = {system.id: system.fingerprint for system in systems}

    def eligible(row: Receipt) -> bool:
        a, b = output_map.get(row.a_output), output_map.get(row.b_output)
        return (
            a is not None
            and b is not None
            and a.case_key == b.case_key == row.case_key
            and a.system_id == row.a_system
            and b.system_id == row.b_system
            and a.system_id != b.system_id
            and fingerprints[a.system_id] == row.a_fingerprint
            and fingerprints[b.system_id] == row.b_fingerprint
            and a.verdict == b.verdict == 'pass'
            and a.content_digest != b.content_digest
        )

    ordinary_rows = tuple(
        row for row in selected if row.repeat_of is None and eligible(row)
    )
    ordinary_map = {row.seq: row for row in ordinary_rows}
    repeat_rows: list[Receipt] = []
    for row in selected:
        if row.repeat_of is None or not eligible(row):
            continue

        original = ordinary_map.get(row.repeat_of)
        if original is not None and (
            row.actor_id == original.actor_id
            and row.a_output == original.b_output
            and row.b_output == original.a_output
        ):
            repeat_rows.append(row)

    def human(row: Receipt, weight: float) -> HumanEvidence:
        return HumanEvidence(
            receipt=str(row.seq),
            actor=row.actor_id,
            session=row.session_id,
            choice=row.choice,
            defects_a=tuple(sorted(row.defects.a)),
            defects_b=tuple(sorted(row.defects.b)),
            length_a=output_map[row.a_output].length,
            length_b=output_map[row.b_output].length,
            comparison=Comparison(
                row.a_system,
                row.b_system,
                ranking.LEFT_SCORE[row.choice],
                row.case_key,
                weight,
            ),
            repeat_of=None if row.repeat_of is None else str(row.repeat_of),
        )

    assigned = actor_weights([row.actor_id for row in ordinary_rows])
    ordinary = tuple(
        human(row, weight) for row, weight in zip(ordinary_rows, assigned, strict=True)
    )
    repeats = tuple(human(row, 0.0) for row in repeat_rows)
    numerical = tuple(
        Settlement.model_validate_json(canonical(row.value)) for row in numerics
    )
    settlements = tuple(
        Comparison(
            output_map[row.a_output].system_id,
            output_map[row.b_output].system_id,
            row.score,
            row.case_key,
            row.weight,
            sided=False,
        )
        for row in numerical
    )
    family = evaluation.manifest.family
    evidence_digest = digest(
        {
            'export_digest': capture.content_digest,
            'ordinary': [
                {
                    'receipt': row.receipt,
                    'actor': row.actor,
                    'weight': row.comparison.weight,
                }
                for row in ordinary
            ],
            'repeats': [row.receipt for row in repeats],
        }
    )
    points = {system.id: Point(score=None, component=None) for system in systems}
    for component in ranking.rate_components(
        [row.comparison for row in ordinary] + list(settlements),
        family.systems,
        family.reference,
    ):
        ratings = component.ratings
        identity = digest(list(ratings.systems))
        points.update(
            {
                system: Point(score=float(ratings.score[index]), component=identity)
                for index, system in enumerate(ratings.systems)
            }
        )

    fitted_points = point_snapshot(
        evaluation.id,
        header.policy_id,
        points,
        source_export_id=header.id,
        source_export_digest=capture.content_digest,
        weighted_evidence_digest=evidence_digest,
    )
    selected_sessions = {row.session for row in ordinary}
    analysis = AnalysisInput(
        family=family.name,
        reference=family.reference,
        systems=family.systems,
        defects=family.defects,
        provenance=LedgerProvenance(
            status='ledger',
            schema_version=header.schema_version,
            export_id=header.id,
            export_digest=capture.content_digest,
            namespace=header.namespace,
            captured_at=datetime.fromtimestamp(header.created_at / 1000, UTC),
            event_cutoff=header.event_cutoff,
            receipt_cutoff=header.receipt_cutoff,
            family_revision=header.family_revision,
            analysis_sequence=header.analysis_sequence,
            evaluation_id=evaluation.id,
            publication_id=publication.id,
            policy_id=header.policy_id,
            sampling_point_snapshot_id=sampling_points.id,
            weighted_evidence_digest=evidence_digest,
            fitted_point_snapshot_id=fitted_points.id,
        ),
        declared_categories=family.categories,
        categories=tuple((case.key, case.category) for case in cases),
        ordinary=ordinary,
        repeats=repeats,
        settlements=settlements,
        outputs=tuple(
            OutputMeasurement(
                case=row.case_key,
                system=row.system_id,
                errored=row.metadata.errored,
                verdict=row.verdict,
                cost_usd=row.metadata.cost_usd,
                latency_sec=row.metadata.latency_sec,
                output_tokens=row.metadata.output_tokens,
            )
            for row in outputs
        ),
        sessions=tuple(
            row.id
            for row in sorted(sessions, key=lambda row: (row.created_at, row.id))
            if row.id in selected_sessions
        ),
        accepted=len(observations),
    )
    return SnapshotAnalysis(
        analysis=analysis,
        fingerprints=fingerprints,
        system_names=publication.presentation.systems,
        points=fitted_points,
    )
