"""A store's vote cuts and moderation, production's cut snapshots, and rounds."""

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime
from itertools import combinations, product
from statistics import median
from typing import Literal, NamedTuple, get_args

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
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
from assistant_bench.database import Database, Param, insert_missing
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


class BallotPublication(_Model):
    """Original facts of a ballot outside the active publication."""

    id: str
    case: CaseFacts
    a: OutputFacts
    b: OutputFacts
    a_name: str
    b_name: str


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
    pair_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    publication: BallotPublication | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    """Omitted for active ballots so existing sealed snapshot digests still verify."""


class Cut(_Model):
    """A store's counted votes that can no longer change, with what they rate."""

    cutoff: int
    """The largest ID among the family's votes older than the undo window, or 0."""

    inventory: Inventory
    votes: tuple[Vote, ...]
    """Counted votes older than the undo window, in ID order."""

    excluded: int
    """Excluded votes older than the undo window."""

    output_generation: str | None = None
    """The immutable collection prepared for this publication, when known."""


class Snapshot(_Model):
    """The self-contained cuts and closing policy of one voted round."""

    family: FamilyName
    taken_at: AwareDatetime
    closed_at: AwareDatetime
    category_order: tuple[Category, ...]
    closing_revision: str | None
    production: Cut | None
    private: Cut | None
    method: Literal['expected_net'] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode='after')
    def validate_cuts(self) -> Snapshot:
        cuts = self.cuts()
        if not cuts:
            raise ValueError('A snapshot requires at least one store cut.')
        cases: set[str] = set()
        outputs: set[str] = set()
        fingerprints: dict[str, str] = {}
        first = self.policy
        names: dict[str, str] = {}
        for _, cut in cuts:
            inventory = cut.inventory
            if inventory.family != self.family:
                raise ValueError('Every cut must belong to the snapshot family.')
            if inventory.cases and (
                inventory.reference != first.reference
                or inventory.defects != first.defects
            ):
                raise ValueError('Store cuts disagree on their frozen family settings.')
            if inventory.cases:
                for system, name in inventory.systems.items():
                    if system in names and names[system] != name:
                        raise ValueError(
                            'Store cuts disagree on a system display name.'
                        )
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
            for output in _output_evidence(cut):
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

    @property
    def policy(self) -> Inventory:
        """Use active family settings when they are available."""
        inventories = [cut.inventory for _, cut in self.cuts()]
        return next(
            (inventory for inventory in inventories if inventory.cases),
            inventories[0],
        )

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


def read_cut(
    database: Database, family: str, now: datetime, *, include_active: bool = True
) -> Cut | None:
    """Read active inventory and eligible ballots in a single SQL read view.

    Each result value holds one fact, keeping it below D1's value size limit.
    A result exceeding the REST response limit fails through the database API.
    """
    rows = database.execute(
        """WITH active_publication AS (
            SELECT publication_id AS id FROM active WHERE family = ? AND ?
        ), selected_votes AS (
            SELECT * FROM votes WHERE family = ? AND recorded_at <= ?
        ), policy AS (
            SELECT * FROM publications WHERE id = coalesce(
                (SELECT id FROM active_publication),
                (SELECT publication_id FROM selected_votes ORDER BY id DESC LIMIT 1)
            )
        ), output_keys AS (
            SELECT o.publication_id, o.output_key FROM outputs AS o
            JOIN active_publication AS p ON p.id = o.publication_id
            UNION
            SELECT publication_id, a_output FROM selected_votes WHERE exclusion IS NULL
            UNION
            SELECT publication_id, b_output FROM selected_votes WHERE exclusion IS NULL
        ), output_facts AS (
            SELECT o.publication_id, o.output_key, o.case_key, json_object(
                'key', o.output_key, 'case_key', o.case_key, 'system', o.system,
                'fingerprint', o.fingerprint, 'verdict', o.verdict,
                'errored', json(CASE WHEN o.errored THEN 'true' ELSE 'false' END),
                'length', o.length, 'content_digest', o.content_digest
            ) AS facts FROM outputs AS o JOIN output_keys USING (
                publication_id, output_key
            )
        ), case_keys AS (
            SELECT c.publication_id, c.case_key FROM cases AS c
            JOIN active_publication AS p ON p.id = c.publication_id
            UNION
            SELECT publication_id, case_key FROM output_facts
        ), case_facts AS (
            SELECT c.publication_id, c.case_key, json_object(
                'key', c.case_key, 'category', c.category, 'split', c.split
            ) AS facts FROM cases AS c JOIN case_keys USING (publication_id, case_key)
        )
        SELECT 'header' AS record_kind, 0 AS sort_rank, '' AS sort_key,
            0 AS sort_id, json_object(
                'id', p.id, 'family', p.family, 'reference', p.reference,
                'systems', json(p.systems), 'defects', json(p.defects),
                'cutoff', (SELECT coalesce(max(id), 0) FROM selected_votes),
                'excluded', (
                    SELECT count(*) FROM selected_votes WHERE exclusion IS NOT NULL
                )
            ) AS payload_json FROM policy AS p
        UNION ALL
        SELECT 'case', 1, c.case_key, 0, c.facts
        FROM case_facts AS c JOIN active_publication AS p ON p.id = c.publication_id
        UNION ALL
        SELECT 'output', 2, o.output_key, 0, o.facts
        FROM output_facts AS o JOIN active_publication AS p ON p.id = o.publication_id
        UNION ALL
        SELECT 'vote', 3, '', v.id, json_object(
            'id', v.id, 'actor', v.actor_id, 'case_key', v.case_key,
            'a_output', v.a_output, 'b_output', v.b_output,
            'a_system', v.a_system, 'b_system', v.b_system, 'choice', v.choice,
            'defects', json(v.defects), 'elapsed_ms', v.elapsed_ms,
            'pair_id', v.pair_id,
            'publication', CASE
                WHEN v.publication_id IS NOT (SELECT id FROM active_publication)
                THEN json_object(
                    'id', p.id, 'case', json(c.facts),
                    'a', json(a.facts), 'b', json(b.facts),
                    'a_name', (
                        SELECT value FROM json_each(p.systems) WHERE key = v.a_system
                    ),
                    'b_name', (
                        SELECT value FROM json_each(p.systems) WHERE key = v.b_system
                    )
                ) END
        ) FROM selected_votes AS v
        JOIN publications AS p ON p.id = v.publication_id
        JOIN output_facts AS a ON a.publication_id = p.id AND a.output_key = v.a_output
        JOIN output_facts AS b ON b.publication_id = p.id AND b.output_key = v.b_output
        JOIN case_facts AS c ON c.publication_id = p.id AND c.case_key = a.case_key
        WHERE v.exclusion IS NULL
        ORDER BY sort_rank, sort_key, sort_id""",
        [
            family,
            int(include_active),
            family,
            int(now.timestamp() * 1000) - UNDO_WINDOW_MS,
        ],
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
            f'{self.column} IN (SELECT value FROM json_each(?))',  # noqa: S608
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

    rows: list[ListedVote] = []
    while True:
        page = database.execute(
            'SELECT id, actor_id AS actor, ip_tag, a_system, b_system, choice, '  # noqa: S608
            'elapsed_ms, recorded_at, exclusion FROM votes '
            f'WHERE {where} AND id > ? ORDER BY id LIMIT ?',
            [*params, rows[-1].id if rows else 0, PAGE_ROWS],
        ).rows
        rows.extend(map(ListedVote.model_validate, page))
        if len(page) < PAGE_ROWS:
            return rows


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
        'UPDATE votes SET exclusion = ? WHERE family = ? AND exclusion IS NULL '  # noqa: S608
        f'AND {condition} RETURNING id',
        [reason, family, param],
    )
    return len(result.rows)


def restore(database: Database, family: str, selector: Selector) -> int:
    """Count the selected excluded votes again, returning how many changed."""
    condition, param = selector.condition()
    result = database.execute(
        # `condition` is built from a literal column name.
        'UPDATE votes SET exclusion = NULL WHERE family = ? '  # noqa: S608
        'AND exclusion IS NOT NULL '
        f'AND {condition} RETURNING id',
        [family, param],
    )
    return len(result.rows)


# ── Snapshots ──


class _SnapshotHeader(_Model):
    id: str
    family: FamilyName
    taken_at: AwareDatetime
    closed_at: AwareDatetime
    category_order_json: Json[tuple[Category, ...]]
    closing_revision: str | None
    method: Literal['expected_net'] | None = None
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
        '(id, family, taken_at, closed_at, category_order_json, closing_revision, '
        'method, expected_store_count, expected_case_count, expected_output_count, '
        'expected_vote_count, manifest_digest) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
        'ON CONFLICT (id) DO NOTHING',
        [
            identity,
            snapshot.family,
            snapshot.taken_at.isoformat(),
            snapshot.closed_at.isoformat(),
            json.dumps(snapshot.category_order),
            snapshot.closing_revision,
            snapshot.method,
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

        insert_missing(
            database,
            'vote_snapshot_cases',
            ('snapshot_id', 'target', 'case_key', 'facts_json'),
            (
                (identity, target, case.key, case.model_dump_json())
                for case in inventory.cases
            ),
        )

        insert_missing(
            database,
            'vote_snapshot_outputs',
            ('snapshot_id', 'target', 'output_key', 'facts_json'),
            (
                (identity, target, output.key, output.model_dump_json())
                for output in inventory.outputs
            ),
        )

        insert_missing(
            database,
            'vote_snapshot_votes',
            ('snapshot_id', 'target', 'vote_id', 'vote_json'),
            ((identity, target, vote.id, vote.model_dump_json()) for vote in cut.votes),
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
            closed_at=header.closed_at,
            category_order=header.category_order_json,
            closing_revision=header.closing_revision,
            method=header.method,
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


class _CountedVote(NamedTuple):
    vote: Vote
    actor: str
    a: OutputFacts
    b: OutputFacts
    case: CaseFacts | None

    def receipt_facts(self) -> tuple[object, ...]:
        """Identify the evidence graded by a submission in either official store."""
        return (
            self.vote.case_key,
            self.vote.choice,
            tuple(sorted(self.vote.defects.a)),
            tuple(sorted(self.vote.defects.b)),
            self.case,
            self.a,
            self.b,
        )


def voting_input(
    snapshot: Snapshot,
    outputs_by_cut: Mapping[Literal['production', 'private'], Sequence[Output]],
) -> AnalysisInput:
    """Use original ballot facts and measure outputs from pinned active collections."""
    cuts = snapshot.cuts()
    cases = [case for _, cut in cuts for case in cut.inventory.cases]
    outputs = [output for _, cut in cuts for output in cut.inventory.outputs]
    counted: list[_CountedVote] = []
    receipts: dict[str, _CountedVote] = {}
    for store, cut in cuts:
        published = {output.key: output for output in cut.inventory.outputs}
        published_cases = {case.key: case for case in cut.inventory.cases}
        for vote in cut.votes:
            if vote.publication is None:
                a, b = published.get(vote.a_output), published.get(vote.b_output)
                case = published_cases.get(vote.case_key)
            else:
                a, b = vote.publication.a, vote.publication.b
                case = vote.publication.case
            if a is None or b is None or not _counts(vote, a, b):
                continue

            row = _CountedVote(vote, f'{store}:{vote.actor}', a, b, case)
            if vote.pair_id is not None:
                previous = receipts.get(vote.pair_id)
                if previous is not None:
                    if previous.receipt_facts() != row.receipt_facts():
                        raise InputError(
                            'Official vote stores disagree on a ballot receipt.'
                        )
                    continue
                receipts[vote.pair_id] = row

            counted.append(row)

    actors = [row.actor for row in counted]
    ordinary = tuple(
        HumanEvidence(
            actor=actor,
            choice=vote.choice,
            defects_a=tuple(sorted(vote.defects.a)),
            defects_b=tuple(sorted(vote.defects.b)),
            length_a=a.length,
            length_b=b.length,
            comparison=ranking.Comparison(
                vote.a_system,
                vote.b_system,
                ranking.LEFT_SCORE[vote.choice],
                vote.case_key,
                weight,
            ),
            category=None
            if vote.publication is None
            else vote.publication.case.category,
            split=None if vote.publication is None else vote.publication.case.split,
        )
        for (vote, actor, a, b, _), weight in zip(
            counted, actor_weights(actors), strict=True
        )
    )

    policy = snapshot.policy
    defects = dict.fromkeys(policy.defects)
    categories = dict.fromkeys(snapshot.category_order)
    for evidence in ordinary:
        if evidence.category is None:
            continue

        categories.setdefault(evidence.category)
        defects.update(dict.fromkeys((*evidence.defects_a, *evidence.defects_b)))

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
        reference=policy.reference,
        systems=tuple(system_names(snapshot)),
        defects=tuple(defects),
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
        declared_categories=tuple(categories),
        categories=tuple((case.key, case.category) for case in cases),
        ordinary=ordinary,
        settlements=_settlements(outputs),
        outputs=tuple(_measurement(row, measured.get(row.key)) for row in outputs),
        dropped=sum(len(cut.votes) + cut.excluded for _, cut in cuts) - len(counted),
        splits=tuple((case.key, case.split) for case in cases),
    )


def fingerprints(snapshot: Snapshot) -> dict[str, str]:
    return {
        output.system: output.fingerprint
        for _, cut in snapshot.cuts()
        for output in _output_evidence(cut)
    }


def _output_evidence(cut: Cut) -> Iterator[OutputFacts]:
    yield from cut.inventory.outputs
    for vote in cut.votes:
        original = vote.publication
        if original is not None and _counts(vote, original.a, original.b):
            yield original.a
            yield original.b


def system_names(snapshot: Snapshot) -> dict[str, str]:
    cuts = snapshot.cuts()
    names = {
        system: name
        for _, cut in cuts
        if cut.inventory.cases
        for system, name in cut.inventory.systems.items()
    }
    for _, cut in cuts:
        for vote in reversed(cut.votes):
            original = vote.publication
            if original is not None and _counts(vote, original.a, original.b):
                names.setdefault(vote.a_system, original.a_name)
                names.setdefault(vote.b_system, original.b_name)
        for system, name in cut.inventory.systems.items():
            names.setdefault(system, name)
    return names


def _counts(vote: Vote, a: OutputFacts, b: OutputFacts) -> bool:
    return (
        a.key == vote.a_output
        and b.key == vote.b_output
        and (vote.publication is None or vote.publication.case.key == vote.case_key)
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
                both_bad = a.verdict == b.verdict == 'fail'
                if both_bad:
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
                    ranking.Comparison(
                        left, right, score, case, weight, sided=False, both_bad=both_bad
                    )
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
