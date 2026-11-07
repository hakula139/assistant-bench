"""A store's vote cuts and moderation, production's cut snapshots, and rounds."""

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from itertools import combinations, product
from statistics import median
from typing import Literal, Protocol, get_args

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Json,
    ValidationError,
    model_validator,
)

from assistant_bench.arena.publication import (
    CaseFacts,
    Inventory,
    OutputFacts,
    output_key,
)
from assistant_bench.database import Database, Param, Row
from assistant_bench.families import Category, Choice, Defect, FamilyName
from assistant_bench.messages import InputError, error_lines
from assistant_bench.outputs import Output
from assistant_bench.report import (
    AnalysisInput,
    Cutoff,
    Cutoffs,
    HumanEvidence,
    OutputMeasurement,
)
from assistant_bench.scoring import ranking
from assistant_bench.scoring.weighting import actor_weights


UNDO_WINDOW_MS = 10 * 60_000
"""How long a vote stays undoable.

It must equal `UNDO_MS` in `web/worker/limits.ts`, so no cut holds an undoable vote.
"""

PAGE_ROWS = 1000


class _Model(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class TaggedDefects(_Model):
    a: tuple[Defect, ...] = ()
    b: tuple[Defect, ...] = ()


class Vote(_Model):
    id: int
    actor: str
    case_key: str
    a_output: str
    b_output: str
    a_system: str
    b_system: str
    choice: Choice
    defects: TaggedDefects
    elapsed_ms: int


class Cut(_Model):
    """A store's counted votes that can no longer change, with what they rate."""

    cutoff: int
    """The largest ID among the family's votes older than the undo window, or 0."""

    inventory: Inventory
    votes: tuple[Vote, ...]
    """Counted votes with IDs up to the cutoff, in ID order."""

    excluded: int
    """Excluded votes with IDs up to the cutoff."""

    output_generation: str | None = None
    """The immutable collection prepared for this publication, when known."""


class Snapshot(_Model):
    """The self-contained cuts and closing policy of one voted round."""

    family: FamilyName
    taken_at: AwareDatetime
    category_order: tuple[Category, ...]
    closing_revision: str | None
    production: Cut | None
    private: Cut | None

    @model_validator(mode='after')
    def validate_cuts(self) -> Snapshot:
        cuts = self.cuts()
        if not cuts:
            raise ValueError('A snapshot requires at least one store cut.')
        cases: set[str] = set()
        outputs: set[str] = set()
        fingerprints: dict[str, str] = {}
        first = cuts[0][1].inventory
        names: dict[str, str] = {}
        for _, cut in cuts:
            inventory = cut.inventory
            if inventory.family != self.family:
                raise ValueError('Every cut must belong to the snapshot family.')
            if (
                inventory.reference != first.reference
                or inventory.defects != first.defects
            ):
                raise ValueError('Store cuts disagree on their frozen family settings.')
            for system, name in inventory.systems.items():
                if system in names and names[system] != name:
                    raise ValueError('Store cuts disagree on a system display name.')
                names[system] = name
            if inventory.reference not in inventory.systems:
                raise ValueError('A cut must name its reference system.')
            case_keys = {case.key for case in inventory.cases}
            output_keys = {output.key for output in inventory.outputs}
            if len(case_keys) != len(inventory.cases) or len(output_keys) != len(
                inventory.outputs
            ):
                raise ValueError('A cut contains duplicate case or output keys.')
            if cases & case_keys or outputs & output_keys:
                raise ValueError('Store cuts must have disjoint case and output keys.')
            for output in inventory.outputs:
                if (
                    output.case_key not in case_keys
                    or output.system not in inventory.systems
                ):
                    raise ValueError(
                        'A cut output must belong to its case and system inventory.'
                    )
                if (
                    output.system in fingerprints
                    and fingerprints[output.system] != output.fingerprint
                ):
                    raise ValueError('Store cuts disagree on a system fingerprint.')
                fingerprints[output.system] = output.fingerprint
            if any(vote.id > cut.cutoff for vote in cut.votes):
                raise ValueError('A cut contains a vote beyond its cutoff.')
            cases.update(case_keys)
            outputs.update(output_keys)
        return self

    def cuts(self) -> tuple[tuple[Literal['production', 'private'], Cut], ...]:
        cuts: list[tuple[Literal['production', 'private'], Cut]] = []
        if self.production is not None:
            cuts.append(('production', self.production))
        if self.private is not None:
            cuts.append(('private', self.private))
        return tuple(cuts)


# ── Reading a Store ──


class _CutHeader(BaseModel):
    id: str
    family: FamilyName
    reference: str
    systems: dict[str, str]
    defects: tuple[Defect, ...]
    cutoff: int
    excluded: int


def read_cut(database: Database, family: str, now: datetime) -> Cut | None:
    """Read one SQL snapshot, or None when the family has no active publication.

    The undo cutoff, exclusions and inventory share the statement's read view.
    Each result value holds one fact, keeping it below D1's value size limit.
    A result exceeding the REST response limit fails through the database API.
    """
    rows = database.execute(
        """WITH active_publication AS (
            SELECT p.* FROM active AS a JOIN publications AS p
            ON p.id = a.publication_id WHERE a.family = ?
        ), bounds AS (
            SELECT coalesce(max(id), 0) AS cutoff FROM votes
            WHERE family = ? AND recorded_at <= ?
        ), selected_votes AS (
            SELECT v.* FROM votes AS v JOIN active_publication AS p
            ON v.family = p.family CROSS JOIN bounds AS b WHERE v.id <= b.cutoff
        )
        SELECT 'header' AS record_kind, 0 AS sort_rank, '' AS sort_key,
            0 AS sort_id, json_object(
                'id', p.id, 'family', p.family, 'reference', p.reference,
                'systems', json(p.systems), 'defects', json(p.defects),
                'cutoff', b.cutoff, 'excluded',
                (SELECT count(*) FROM selected_votes WHERE exclusion IS NOT NULL)
            ) AS payload_json FROM active_publication AS p CROSS JOIN bounds AS b
        UNION ALL
        SELECT 'case', 1, c.case_key, 0, json_object(
            'key', c.case_key, 'category', c.category, 'split', c.split
        ) FROM cases AS c JOIN active_publication AS p ON p.id = c.publication_id
        UNION ALL
        SELECT 'output', 2, o.output_key, 0, json_object(
            'key', o.output_key, 'case_key', o.case_key, 'system', o.system,
            'fingerprint', o.fingerprint, 'verdict', o.verdict,
            'errored', json(CASE WHEN o.errored THEN 'true' ELSE 'false' END),
            'length', o.length, 'content_digest', o.content_digest
        ) FROM outputs AS o JOIN active_publication AS p ON p.id = o.publication_id
        UNION ALL
        SELECT 'vote', 3, '', id, json_object(
            'id', id, 'actor', actor_id, 'case_key', case_key,
            'a_output', a_output, 'b_output', b_output,
            'a_system', a_system, 'b_system', b_system, 'choice', choice,
            'defects', json(defects), 'elapsed_ms', elapsed_ms
        ) FROM selected_votes WHERE exclusion IS NULL
        ORDER BY sort_rank, sort_key, sort_id""",
        [family, family, int(now.timestamp() * 1000) - UNDO_WINDOW_MS],
    ).rows
    if not rows:
        return None
    header = _CutHeader.model_validate_json(str(rows[0]['payload_json']))
    return Cut(
        cutoff=header.cutoff,
        excluded=header.excluded,
        inventory=Inventory(
            id=header.id,
            family=header.family,
            reference=header.reference,
            systems=header.systems,
            defects=header.defects,
            cases=tuple(
                CaseFacts.model_validate_json(str(row['payload_json']))
                for row in rows
                if row['record_kind'] == 'case'
            ),
            outputs=tuple(
                OutputFacts.model_validate_json(str(row['payload_json']))
                for row in rows
                if row['record_kind'] == 'output'
            ),
        ),
        votes=tuple(
            Vote.model_validate_json(str(row['payload_json']))
            for row in rows
            if row['record_kind'] == 'vote'
        ),
    )


class _Keyed(Protocol):
    @property
    def id(self) -> int: ...


def _paged[T: _Keyed](
    database: Database,
    parse: Callable[[Row], T],
    columns: str,
    where: str,
    params: Sequence[Param],
) -> list[T]:
    """Select `columns`, which include `id`, from the votes `where` holds, in ID order.

    Each request reads one keyset page of at most `PAGE_ROWS` rows.
    """
    rows: list[T] = []
    while True:
        page = database.execute(
            f'SELECT {columns} FROM votes WHERE {where} AND id > ? '
            f'ORDER BY id LIMIT {PAGE_ROWS}',
            [*params, rows[-1].id if rows else 0],
        ).rows
        rows.extend(map(parse, page))
        if len(page) < PAGE_ROWS:
            return rows


class FamilyStatus(_Model):
    family: str
    publication_id: str
    activated_at: int
    """Milliseconds since the epoch."""

    case_count: int
    output_count: int
    votes: int
    """Counted votes on every publication of the family."""

    excluded: int
    voters: int
    """Browsers with a counted vote."""

    latest_vote_at: int | None
    """Milliseconds since the epoch, null before the first vote."""


def status(database: Database, family: str | None) -> list[FamilyStatus]:
    """Summarize each active family, or only the named one, in name order."""
    rows = database.execute(
        'SELECT active.family, active.publication_id, active.activated_at, '
        'publications.case_count, publications.output_count, '
        '(SELECT count(*) FROM votes WHERE votes.family = active.family '
        'AND exclusion IS NULL) AS votes, '
        '(SELECT count(*) FROM votes WHERE votes.family = active.family '
        'AND exclusion IS NOT NULL) AS excluded, '
        '(SELECT count(DISTINCT actor_id) FROM votes '
        'WHERE votes.family = active.family AND exclusion IS NULL) AS voters, '
        '(SELECT max(recorded_at) FROM votes '
        'WHERE votes.family = active.family) AS latest_vote_at '
        'FROM active JOIN publications ON publications.id = active.publication_id '
        'WHERE ? IS NULL OR active.family = ? ORDER BY active.family',
        [family, family],
    ).rows
    return [FamilyStatus.model_validate(row) for row in rows]


# ── Moderation ──


Grouping = Literal['ip', 'actor']

CHOICES: tuple[Choice, ...] = get_args(Choice.__value__)


class Selector(_Model):
    """The votes of one network tag or one browser, or the votes with given IDs."""

    column: Literal['ip_tag', 'actor_id', 'id']
    values: tuple[str, ...] | tuple[int, ...]

    def condition(self) -> tuple[str, Param]:
        """Return the SQL condition and its one parameter, which binds any count."""
        return (
            f'{self.column} IN (SELECT value FROM json_each(?))',
            json.dumps(self.values),
        )


class ListedVote(_Model):
    id: int
    actor: str
    ip_tag: str | None
    """Null once retention cleared it."""

    a_system: str
    b_system: str
    choice: Choice
    elapsed_ms: int
    recorded_at: int
    """Milliseconds since the epoch."""

    exclusion: str | None


class Group(_Model):
    """One network tag's or one browser's votes in a family, excluded ones included."""

    subject: str | None
    """The network tag or browser, or null for votes whose tag retention cleared."""

    votes: int
    peers: int
    """The browsers behind a network tag, or the network tags of a browser."""

    shares: dict[Choice, float]
    median_elapsed_ms: float
    first_at: int
    last_at: int
    excluded: int


def listed(
    database: Database, family: str, selector: Selector | None
) -> list[ListedVote]:
    """Return the family's votes, or only the selected ones, in ID order."""
    where = 'family = ?'
    params: list[Param] = [family]
    if selector is not None:
        condition, param = selector.condition()
        where = f'{where} AND {condition}'
        params.append(param)

    return _paged(
        database,
        ListedVote.model_validate,
        'id, actor_id AS actor, ip_tag, a_system, b_system, choice, elapsed_ms, '
        'recorded_at, exclusion',
        where,
        params,
    )


def groups(database: Database, family: str, by: Grouping) -> list[Group]:
    """Summarize the family's votes per network tag or per browser, most votes first."""
    grouped: defaultdict[str | None, list[ListedVote]] = defaultdict(list)
    for vote in listed(database, family, None):
        grouped[vote.ip_tag if by == 'ip' else vote.actor].append(vote)

    summaries = [
        Group(
            subject=subject,
            votes=len(rows),
            peers=len(
                {row.actor for row in rows}
                if by == 'ip'
                else {row.ip_tag for row in rows if row.ip_tag is not None}
            ),
            shares={
                choice: sum(row.choice == choice for row in rows) / len(rows)
                for choice in CHOICES
            },
            median_elapsed_ms=median(row.elapsed_ms for row in rows),
            first_at=min(row.recorded_at for row in rows),
            last_at=max(row.recorded_at for row in rows),
            excluded=sum(row.exclusion is not None for row in rows),
        )
        for subject, rows in grouped.items()
    ]
    return sorted(
        summaries,
        key=lambda group: (-group.votes, group.subject is None, group.subject or ''),
    )


def check_reason(reason: str) -> None:
    if not reason.strip():
        raise InputError('Give a reason for the exclusion.')


def exclude(database: Database, family: str, selector: Selector, reason: str) -> int:
    """Exclude the selected votes for `reason`, returning how many changed.

    A vote excluded earlier keeps its first reason.

    Raises
    ------
    InputError
        The reason is blank.
    """
    check_reason(reason)
    condition, param = selector.condition()
    result = database.execute(
        'UPDATE votes SET exclusion = ? WHERE family = ? AND exclusion IS NULL '
        f'AND {condition} RETURNING id',
        [reason, family, param],
    )
    return len(result.rows)


def restore(database: Database, family: str, selector: Selector) -> int:
    """Count the selected excluded votes again, returning how many changed."""
    condition, param = selector.condition()
    result = database.execute(
        'UPDATE votes SET exclusion = NULL WHERE family = ? AND exclusion IS NOT NULL '
        f'AND {condition} RETURNING id',
        [family, param],
    )
    return len(result.rows)


# ── Snapshots ──


class _SnapshotHeader(_Model):
    id: str
    family: FamilyName
    taken_at: AwareDatetime
    category_order_json: Json[tuple[Category, ...]]
    closing_revision: str | None
    expected_store_count: int
    expected_case_count: int
    expected_output_count: int
    expected_vote_count: int
    manifest_digest: str
    sealed: bool


class _SnapshotStore(_Model):
    target: Literal['production', 'private']
    publication_id: str
    reference: str
    systems_json: Json[dict[str, str]]
    defects_json: Json[tuple[Defect, ...]]
    cutoff: int
    excluded: int
    output_generation_id: str | None


def _snapshot_digest(snapshot: Snapshot) -> str:
    canonical = json.dumps(
        snapshot.model_dump(mode='json'),
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def save_snapshot(database: Database, snapshot: Snapshot) -> str:
    """Stage immutable fact rows, verify their digest and counts, then seal them.

    Interrupted staging can resume with the same content. A sealed snapshot is
    readable by its content ID, and no row can be added or changed after sealing.
    """
    identity = _snapshot_digest(snapshot)
    existing = database.execute(
        'SELECT sealed FROM vote_snapshots WHERE id = ?', [identity]
    ).rows
    if existing and existing[0]['sealed']:
        if read_snapshot(database, identity) != snapshot:
            raise InputError(f'Snapshot `{identity}` conflicts with its stored facts.')
        return identity
    cuts = snapshot.cuts()
    counts = (
        len(cuts),
        sum(len(cut.inventory.cases) for _, cut in cuts),
        sum(len(cut.inventory.outputs) for _, cut in cuts),
        sum(len(cut.votes) for _, cut in cuts),
    )
    database.execute(
        'INSERT INTO vote_snapshots '
        '(id, family, taken_at, category_order_json, closing_revision, '
        'expected_store_count, expected_case_count, expected_output_count, '
        'expected_vote_count, manifest_digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
        'ON CONFLICT (id) DO NOTHING',
        [
            identity,
            snapshot.family,
            snapshot.taken_at.isoformat(),
            json.dumps(snapshot.category_order),
            snapshot.closing_revision,
            *counts,
            identity,
        ],
    )
    for target, cut in cuts:
        inventory = cut.inventory
        database.execute(
            'INSERT INTO vote_snapshot_stores '
            '(snapshot_id, target, publication_id, reference, systems_json, '
            'defects_json, cutoff, excluded, output_generation_id) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) '
            'ON CONFLICT (snapshot_id, target) DO NOTHING',
            [
                identity,
                target,
                inventory.id,
                inventory.reference,
                json.dumps(inventory.systems),
                json.dumps(inventory.defects),
                cut.cutoff,
                cut.excluded,
                cut.output_generation,
            ],
        )
        for case in inventory.cases:
            database.execute(
                'INSERT INTO vote_snapshot_cases '
                '(snapshot_id, target, case_key, facts_json) VALUES (?, ?, ?, ?) '
                'ON CONFLICT (snapshot_id, target, case_key) DO NOTHING',
                [identity, target, case.key, case.model_dump_json()],
            )
        for output in inventory.outputs:
            database.execute(
                'INSERT INTO vote_snapshot_outputs '
                '(snapshot_id, target, output_key, facts_json) VALUES (?, ?, ?, ?) '
                'ON CONFLICT (snapshot_id, target, output_key) DO NOTHING',
                [identity, target, output.key, output.model_dump_json()],
            )
        for vote in cut.votes:
            database.execute(
                'INSERT INTO vote_snapshot_votes '
                '(snapshot_id, target, vote_id, vote_json) VALUES (?, ?, ?, ?) '
                'ON CONFLICT (snapshot_id, target, vote_id) DO NOTHING',
                [identity, target, vote.id, vote.model_dump_json()],
            )
    if _read_snapshot(database, identity, sealed=False) != snapshot:
        raise InputError(f'Snapshot `{identity}` conflicts with its stored facts.')
    database.execute(
        'UPDATE vote_snapshots SET sealed = 1 WHERE id = ? AND sealed = 0 '
        'AND expected_store_count = '
        '(SELECT count(*) FROM vote_snapshot_stores WHERE snapshot_id = ?) '
        'AND expected_case_count = '
        '(SELECT count(*) FROM vote_snapshot_cases WHERE snapshot_id = ?) '
        'AND expected_output_count = '
        '(SELECT count(*) FROM vote_snapshot_outputs WHERE snapshot_id = ?) '
        'AND expected_vote_count = '
        '(SELECT count(*) FROM vote_snapshot_votes WHERE snapshot_id = ?)',
        [identity] * 5,
    )
    read_snapshot(database, identity)
    return identity


def read_snapshot(database: Database, identity: str) -> Snapshot:
    """Read only a sealed snapshot and verify its canonical digest and row counts."""
    return _read_snapshot(database, identity, sealed=True)


def _read_snapshot(database: Database, identity: str, *, sealed: bool) -> Snapshot:
    headers = database.execute(
        'SELECT * FROM vote_snapshots WHERE id = ?', [identity]
    ).rows
    if not headers or (sealed and not headers[0]['sealed']):
        raise InputError(f'Snapshot `{identity}` is not sealed in the private store.')
    try:
        header = _SnapshotHeader.model_validate(headers[0])
        stores = [
            _SnapshotStore.model_validate(row)
            for row in database.execute(
                'SELECT target, publication_id, reference, systems_json, '
                'defects_json, cutoff, excluded, output_generation_id '
                'FROM vote_snapshot_stores WHERE snapshot_id = ? ORDER BY target',
                [identity],
            ).rows
        ]
        cuts: dict[Literal['production', 'private'], Cut] = {}
        for store in stores:
            parameters = [identity, store.target]
            cases = tuple(
                CaseFacts.model_validate_json(str(row['facts_json']))
                for row in database.execute(
                    'SELECT facts_json FROM vote_snapshot_cases '
                    'WHERE snapshot_id = ? AND target = ? ORDER BY case_key',
                    parameters,
                ).rows
            )
            outputs = tuple(
                OutputFacts.model_validate_json(str(row['facts_json']))
                for row in database.execute(
                    'SELECT facts_json FROM vote_snapshot_outputs '
                    'WHERE snapshot_id = ? AND target = ? ORDER BY output_key',
                    parameters,
                ).rows
            )
            votes = tuple(
                Vote.model_validate_json(str(row['vote_json']))
                for row in database.execute(
                    'SELECT vote_json FROM vote_snapshot_votes '
                    'WHERE snapshot_id = ? AND target = ? ORDER BY vote_id',
                    parameters,
                ).rows
            )
            cuts[store.target] = Cut(
                inventory=Inventory(
                    id=store.publication_id,
                    family=header.family,
                    reference=store.reference,
                    systems=store.systems_json,
                    defects=store.defects_json,
                    cases=cases,
                    outputs=outputs,
                ),
                cutoff=store.cutoff,
                excluded=store.excluded,
                votes=votes,
                output_generation=store.output_generation_id,
            )
        snapshot = Snapshot(
            family=header.family,
            taken_at=header.taken_at,
            category_order=header.category_order_json,
            closing_revision=header.closing_revision,
            production=cuts.get('production'),
            private=cuts.get('private'),
        )
    except ValidationError as error:
        raise InputError(error_lines(error, f'Snapshot `{identity}`')) from error
    expected = (
        header.expected_store_count,
        header.expected_case_count,
        header.expected_output_count,
        header.expected_vote_count,
    )
    actual = (
        len(cuts),
        sum(len(c.inventory.cases) for c in cuts.values()),
        sum(len(c.inventory.outputs) for c in cuts.values()),
        sum(len(c.votes) for c in cuts.values()),
    )
    if (
        expected != actual
        or _snapshot_digest(snapshot) != identity
        or header.manifest_digest != identity
    ):
        raise InputError(f'Snapshot `{identity}` has incomplete or conflicting facts.')
    return snapshot


# ── Closing ──


def voting_input(
    snapshot: Snapshot,
    outputs_by_cut: Mapping[Literal['production', 'private'], Sequence[Output]],
) -> AnalysisInput:
    """Build the round's input from frozen cuts and their pinned output metrics.

    A vote counts when both its outputs passed in the cut's publication, on the
    vote's case, with different content. Latency, tokens and cost come from
    `outputs_by_cut`, matched by output key. Missing collection pins or outputs
    measure as null.
    """
    cuts = snapshot.cuts()
    inventory = cuts[0][1].inventory.model_copy(
        update={
            'cases': tuple(case for _, cut in cuts for case in cut.inventory.cases),
            'outputs': tuple(
                output for _, cut in cuts for output in cut.inventory.outputs
            ),
            'systems': system_names(snapshot),
        }
    )
    published = {output.key: output for output in inventory.outputs}
    counted: list[Vote] = []
    for store, cut in cuts:
        eligible = {output.key: output for output in cut.inventory.outputs}
        counted.extend(
            vote.model_copy(update={'actor': f'{store}:{vote.actor}'})
            for vote in cut.votes
            if _counts(vote, eligible)
        )
    actors = [vote.actor for vote in counted]
    ordinary = tuple(
        HumanEvidence(
            actor=vote.actor,
            choice=vote.choice,
            defects_a=tuple(sorted(vote.defects.a)),
            defects_b=tuple(sorted(vote.defects.b)),
            length_a=published[vote.a_output].length,
            length_b=published[vote.b_output].length,
            comparison=ranking.Comparison(
                vote.a_system,
                vote.b_system,
                ranking.LEFT_SCORE[vote.choice],
                vote.case_key,
                weight,
            ),
        )
        for vote, weight in zip(counted, actor_weights(actors), strict=True)
    )

    measured: dict[str, Output] = {}
    for target, cut in cuts:
        if cut.output_generation is None:
            continue
        available = {
            output_key(snapshot.family, output): output
            for output in outputs_by_cut.get(target, ())
        }
        measured.update(
            (row.key, available[row.key])
            for row in cut.inventory.outputs
            if row.key in available
        )
    return AnalysisInput(
        family=snapshot.family,
        reference=inventory.reference,
        systems=tuple(inventory.systems),
        defects=inventory.defects,
        cutoffs=Cutoffs(
            production=None
            if snapshot.production is None
            else Cutoff(
                id=snapshot.production.cutoff, votes=len(snapshot.production.votes)
            ),
            private=None
            if snapshot.private is None
            else Cutoff(id=snapshot.private.cutoff, votes=len(snapshot.private.votes)),
        ),
        declared_categories=snapshot.category_order,
        categories=tuple((case.key, case.category) for case in inventory.cases),
        ordinary=ordinary,
        settlements=_settlements(inventory.outputs),
        outputs=tuple(
            _measurement(row, measured.get(row.key)) for row in inventory.outputs
        ),
        dropped=sum(len(cut.votes) + cut.excluded for _, cut in cuts) - len(counted),
        splits=tuple((case.key, case.split) for case in inventory.cases),
    )


def fingerprints(snapshot: Snapshot) -> dict[str, str]:
    return {
        output.system: output.fingerprint
        for _, cut in snapshot.cuts()
        for output in cut.inventory.outputs
    }


def system_names(snapshot: Snapshot) -> dict[str, str]:
    return {
        system: name
        for _, cut in snapshot.cuts()
        for system, name in cut.inventory.systems.items()
    }


def _counts(vote: Vote, published: dict[str, OutputFacts]) -> bool:
    a, b = published.get(vote.a_output), published.get(vote.b_output)
    return (
        a is not None
        and b is not None
        and a.case_key == b.case_key == vote.case_key
        and a.system == vote.a_system
        and b.system == vote.b_system
        and a.system != b.system
        and a.verdict == b.verdict == 'pass'
        and a.content_digest != b.content_digest
    )


def _settlements(outputs: Sequence[OutputFacts]) -> tuple[ranking.Comparison, ...]:
    """Settle each pairing a verdict or identical content decides.

    A failing output loses, two failing outputs are both bad, and identical
    passing outputs tie. Ungraded outputs sit out, and each pairing of two
    systems' graded samples on a case weighs 1 / (the product of their counts).
    """
    grouped: defaultdict[str, defaultdict[str, list[OutputFacts]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for output in sorted(outputs, key=lambda row: row.key):
        if output.verdict != 'ungraded':
            grouped[output.case_key][output.system].append(output)

    rows: list[ranking.Comparison] = []
    for case, systems in sorted(grouped.items()):
        for left, right in combinations(sorted(systems), 2):
            weight = 1 / (len(systems[left]) * len(systems[right]))
            for a, b in product(systems[left], systems[right]):
                if a.verdict == b.verdict == 'fail':
                    score = 0.5
                elif a.verdict == 'fail':
                    score = 0.0
                elif b.verdict == 'fail':
                    score = 1.0
                elif a.content_digest == b.content_digest:
                    score = 0.5
                else:
                    continue

                rows.append(
                    ranking.Comparison(left, right, score, case, weight, sided=False)
                )

    return tuple(rows)


def _measurement(row: OutputFacts, output: Output | None) -> OutputMeasurement:
    return OutputMeasurement(
        case=row.case_key,
        system=row.system,
        errored=row.errored,
        verdict=row.verdict,
        reward=None if output is None else output.reward,
        input_tokens=None if output is None else output.input_tokens,
        output_tokens=None if output is None else output.output_tokens,
        cost_usd=None if output is None else output.cost_usd,
        latency_sec=None if output is None else output.latency_sec,
    )
