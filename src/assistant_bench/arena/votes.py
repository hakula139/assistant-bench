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
from sqlalchemy import (
    ColumnElement,
    and_,
    case as sql_case,
    func,
    literal,
    select,
    union,
    union_all,
    update,
)
from sqlalchemy.dialects import sqlite

from assistant_bench import schema
from assistant_bench.arena.publication import (
    CaseFacts,
    Inventory,
    OutputFacts,
    output_key,
)
from assistant_bench.database import (
    Database,
    execute,
    fetch_all,
    fetch_scalar,
    insert_missing,
)
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
    active = schema.Active
    votes_table = schema.Votes
    publications = schema.Publications
    outputs_table = schema.Outputs
    cases_table = schema.Cases

    cutoff_ms = int(now.timestamp() * 1000) - UNDO_WINDOW_MS
    active_publication = (
        select(active.publication_id.label('id'))
        .where(active.family == family, literal(int(include_active)) != 0)
        .cte('active_publication')
    )
    selected_votes = (
        select(votes_table)
        .where(votes_table.family == family, votes_table.recorded_at <= cutoff_ms)
        .cte('selected_votes')
    )
    policy = (
        select(publications)
        .where(
            publications.id
            == func.coalesce(
                select(active_publication.c.id).scalar_subquery(),
                select(selected_votes.c.publication_id)
                .order_by(selected_votes.c.id.desc())
                .limit(1)
                .scalar_subquery(),
            )
        )
        .cte('policy')
    )
    output_keys = union(
        select(outputs_table.publication_id, outputs_table.output_key).join(
            active_publication, active_publication.c.id == outputs_table.publication_id
        ),
        select(selected_votes.c.publication_id, selected_votes.c.a_output).where(
            selected_votes.c.exclusion.is_(None)
        ),
        select(selected_votes.c.publication_id, selected_votes.c.b_output).where(
            selected_votes.c.exclusion.is_(None)
        ),
    ).cte('output_keys')
    output_facts = (
        select(
            outputs_table.publication_id,
            outputs_table.output_key,
            outputs_table.case_key,
            func.json_object(
                'key',
                outputs_table.output_key,
                'case_key',
                outputs_table.case_key,
                'system',
                outputs_table.system,
                'fingerprint',
                outputs_table.fingerprint,
                'verdict',
                outputs_table.verdict,
                'errored',
                func.json(
                    sql_case((outputs_table.errored != 0, 'true'), else_='false')
                ),
                'length',
                outputs_table.length,
                'content_digest',
                outputs_table.content_digest,
            ).label('facts'),
        )
        .join(
            output_keys,
            and_(
                outputs_table.publication_id == output_keys.c.publication_id,
                outputs_table.output_key == output_keys.c.output_key,
            ),
        )
        .cte('output_facts')
    )
    case_keys = union(
        select(cases_table.publication_id, cases_table.case_key).join(
            active_publication, active_publication.c.id == cases_table.publication_id
        ),
        select(output_facts.c.publication_id, output_facts.c.case_key),
    ).cte('case_keys')
    case_facts = (
        select(
            cases_table.publication_id,
            cases_table.case_key,
            func.json_object(
                'key',
                cases_table.case_key,
                'category',
                cases_table.category,
                'split',
                cases_table.split,
            ).label('facts'),
        )
        .join(
            case_keys,
            and_(
                cases_table.publication_id == case_keys.c.publication_id,
                cases_table.case_key == case_keys.c.case_key,
            ),
        )
        .cte('case_facts')
    )

    header_rows = select(
        literal('header').label('record_kind'),
        literal(0).label('sort_rank'),
        literal('').label('sort_key'),
        literal(0).label('sort_id'),
        func.json_object(
            'id',
            policy.c.id,
            'family',
            policy.c.family,
            'reference',
            policy.c.reference,
            'systems',
            func.json(policy.c.systems),
            'defects',
            func.json(policy.c.defects),
            'cutoff',
            select(func.coalesce(func.max(selected_votes.c.id), 0)).scalar_subquery(),
            'excluded',
            select(func.count())
            .select_from(selected_votes)
            .where(selected_votes.c.exclusion.is_not(None))
            .scalar_subquery(),
        ).label('payload_json'),
    ).select_from(policy)

    case_rows = select(
        literal('case'),
        literal(1),
        case_facts.c.case_key,
        literal(0),
        case_facts.c.facts,
    ).join(active_publication, active_publication.c.id == case_facts.c.publication_id)

    output_rows = select(
        literal('output'),
        literal(2),
        output_facts.c.output_key,
        literal(0),
        output_facts.c.facts,
    ).join(active_publication, active_publication.c.id == output_facts.c.publication_id)

    a_facts = output_facts.alias('a')
    b_facts = output_facts.alias('b')
    a_systems = func.json_each(publications.systems).table_valued('key', 'value')
    b_systems = func.json_each(publications.systems).table_valued('key', 'value')
    vote_rows = (
        select(
            literal('vote'),
            literal(3),
            literal(''),
            selected_votes.c.id,
            func.json_object(
                'id',
                selected_votes.c.id,
                'actor',
                selected_votes.c.actor_id,
                'case_key',
                selected_votes.c.case_key,
                'a_output',
                selected_votes.c.a_output,
                'b_output',
                selected_votes.c.b_output,
                'a_system',
                selected_votes.c.a_system,
                'b_system',
                selected_votes.c.b_system,
                'choice',
                selected_votes.c.choice,
                'defects',
                func.json(selected_votes.c.defects),
                'elapsed_ms',
                selected_votes.c.elapsed_ms,
                'pair_id',
                selected_votes.c.pair_id,
                'publication',
                sql_case(
                    (
                        selected_votes.c.publication_id.is_distinct_from(
                            select(active_publication.c.id).scalar_subquery()
                        ),
                        func.json_object(
                            'id',
                            publications.id,
                            'case',
                            func.json(case_facts.c.facts),
                            'a',
                            func.json(a_facts.c.facts),
                            'b',
                            func.json(b_facts.c.facts),
                            'a_name',
                            select(a_systems.c.value)
                            .where(a_systems.c.key == selected_votes.c.a_system)
                            .correlate(publications, selected_votes)
                            .scalar_subquery(),
                            'b_name',
                            select(b_systems.c.value)
                            .where(b_systems.c.key == selected_votes.c.b_system)
                            .correlate(publications, selected_votes)
                            .scalar_subquery(),
                        ),
                    )
                ),
            ),
        )
        .select_from(selected_votes)
        .join(publications, publications.id == selected_votes.c.publication_id)
        .join(
            a_facts,
            and_(
                a_facts.c.publication_id == publications.id,
                a_facts.c.output_key == selected_votes.c.a_output,
            ),
        )
        .join(
            b_facts,
            and_(
                b_facts.c.publication_id == publications.id,
                b_facts.c.output_key == selected_votes.c.b_output,
            ),
        )
        .join(
            case_facts,
            and_(
                case_facts.c.publication_id == publications.id,
                case_facts.c.case_key == a_facts.c.case_key,
            ),
        )
        .where(selected_votes.c.exclusion.is_(None))
    )

    combined = union_all(header_rows, case_rows, output_rows, vote_rows)
    rows = execute(
        database,
        combined.order_by(
            combined.selected_columns.sort_rank,
            combined.selected_columns.sort_key,
            combined.selected_columns.sort_id,
        ),
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
    active = schema.Active
    publications = schema.Publications
    votes_table = schema.Votes
    stmt = (
        select(
            active.family,
            active.publication_id,
            active.activated_at,
            publications.case_count,
            publications.output_count,
            select(func.count())
            .select_from(votes_table)
            .where(votes_table.family == active.family, votes_table.exclusion.is_(None))
            .correlate(active)
            .scalar_subquery()
            .label('votes'),
            select(func.count())
            .select_from(votes_table)
            .where(
                votes_table.family == active.family,
                votes_table.exclusion.is_not(None),
            )
            .correlate(active)
            .scalar_subquery()
            .label('excluded'),
            select(func.count(votes_table.actor_id.distinct()))
            .where(votes_table.family == active.family, votes_table.exclusion.is_(None))
            .correlate(active)
            .scalar_subquery()
            .label('voters'),
            select(func.max(votes_table.recorded_at))
            .where(votes_table.family == active.family)
            .correlate(active)
            .scalar_subquery()
            .label('latest_vote_at'),
        )
        .join(publications, publications.id == active.publication_id)
        .order_by(active.family)
    )
    if family is not None:
        stmt = stmt.where(active.family == family)
    return fetch_all(database, stmt, FamilyStatus)


# ── Moderation ──


Grouping = Literal['ip', 'actor']

CHOICES: tuple[Choice, ...] = get_args(Choice.__value__)


class Selector(_Model):
    """The votes of one network tag or one browser, or the votes with given IDs."""

    column: Literal['ip_tag', 'actor_id', 'id']
    values: tuple[str, ...] | tuple[int, ...]

    def condition(self) -> ColumnElement[bool]:
        """Return the SQL condition, binding any count in one parameter."""
        values = func.json_each(json.dumps(self.values)).table_valued('value')
        subquery = select(values.c.value)
        match self.column:
            case 'ip_tag':
                return schema.Votes.ip_tag.in_(subquery)
            case 'actor_id':
                return schema.Votes.actor_id.in_(subquery)
            case 'id':
                return schema.Votes.id.in_(subquery)


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
    votes_table = schema.Votes
    filters: list[ColumnElement[bool]] = [votes_table.family == family]
    if selector is not None:
        filters.append(selector.condition())

    rows: list[ListedVote] = []
    while True:
        page = fetch_all(
            database,
            select(
                votes_table.id,
                votes_table.actor_id.label('actor'),
                votes_table.ip_tag,
                votes_table.a_system,
                votes_table.b_system,
                votes_table.choice,
                votes_table.elapsed_ms,
                votes_table.recorded_at,
                votes_table.exclusion,
            )
            .where(*filters, votes_table.id > (rows[-1].id if rows else 0))
            .order_by(votes_table.id)
            .limit(PAGE_ROWS),
            ListedVote,
        )
        rows.extend(page)
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
    votes_table = schema.Votes
    result = execute(
        database,
        update(votes_table)
        .values(exclusion=reason)
        .where(
            votes_table.family == family,
            votes_table.exclusion.is_(None),
            selector.condition(),
        )
        .returning(votes_table.id),
    )
    return len(result.rows)


def restore(database: Database, family: str, selector: Selector) -> int:
    """Count the selected excluded votes again, returning how many changed."""
    votes_table = schema.Votes
    result = execute(
        database,
        update(votes_table)
        .values(exclusion=None)
        .where(
            votes_table.family == family,
            votes_table.exclusion.is_not(None),
            selector.condition(),
        )
        .returning(votes_table.id),
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
    snapshots = schema.VoteSnapshots
    if fetch_scalar(
        database,
        select(snapshots.sealed).where(snapshots.id == identity),
        bool,
    ):
        if read_snapshot(database, identity) != snapshot:
            raise InputError(f'Snapshot `{identity}` conflicts with its stored facts.')
        return identity

    cuts = snapshot.cuts()
    execute(
        database,
        sqlite.insert(snapshots)
        .values(
            id=identity,
            family=snapshot.family,
            taken_at=snapshot.taken_at.isoformat(),
            closed_at=snapshot.closed_at.isoformat(),
            category_order_json=json.dumps(snapshot.category_order),
            closing_revision=snapshot.closing_revision,
            method=snapshot.method,
            expected_store_count=len(cuts),
            expected_case_count=sum(len(cut.inventory.cases) for _, cut in cuts),
            expected_output_count=sum(len(cut.inventory.outputs) for _, cut in cuts),
            expected_vote_count=sum(len(cut.votes) for _, cut in cuts),
            manifest_digest=identity,
        )
        .on_conflict_do_nothing(index_elements=[snapshots.id]),
    )

    stores = schema.VoteSnapshotStores
    for target, cut in cuts:
        inventory = cut.inventory
        execute(
            database,
            sqlite.insert(stores)
            .values(
                snapshot_id=identity,
                target=target,
                publication_id=inventory.id,
                reference=inventory.reference,
                systems_json=json.dumps(inventory.systems),
                defects_json=json.dumps(inventory.defects),
                cutoff=cut.cutoff,
                excluded=cut.excluded,
                output_generation_id=cut.output_generation,
            )
            .on_conflict_do_nothing(index_elements=[stores.snapshot_id, stores.target]),
        )

        insert_missing(
            database,
            schema.VoteSnapshotCases,
            (
                {
                    'snapshot_id': identity,
                    'target': target,
                    'case_key': case_row.key,
                    'facts_json': case_row.model_dump_json(),
                }
                for case_row in inventory.cases
            ),
        )

        insert_missing(
            database,
            schema.VoteSnapshotOutputs,
            (
                {
                    'snapshot_id': identity,
                    'target': target,
                    'output_key': output.key,
                    'facts_json': output.model_dump_json(),
                }
                for output in inventory.outputs
            ),
        )

        insert_missing(
            database,
            schema.VoteSnapshotVotes,
            (
                {
                    'snapshot_id': identity,
                    'target': target,
                    'vote_id': vote.id,
                    'vote_json': vote.model_dump_json(),
                }
                for vote in cut.votes
            ),
        )

    if _read_snapshot(database, identity, sealed=False) != snapshot:
        raise InputError(f'Snapshot `{identity}` conflicts with its stored facts.')

    execute(
        database,
        update(snapshots)
        .values(sealed=1)
        .where(
            snapshots.id == identity,
            snapshots.sealed == 0,
            snapshots.expected_store_count
            == select(func.count())
            .select_from(stores)
            .where(stores.snapshot_id == identity)
            .scalar_subquery(),
            snapshots.expected_case_count
            == select(func.count())
            .select_from(schema.VoteSnapshotCases)
            .where(schema.VoteSnapshotCases.snapshot_id == identity)
            .scalar_subquery(),
            snapshots.expected_output_count
            == select(func.count())
            .select_from(schema.VoteSnapshotOutputs)
            .where(schema.VoteSnapshotOutputs.snapshot_id == identity)
            .scalar_subquery(),
            snapshots.expected_vote_count
            == select(func.count())
            .select_from(schema.VoteSnapshotVotes)
            .where(schema.VoteSnapshotVotes.snapshot_id == identity)
            .scalar_subquery(),
        ),
    )
    read_snapshot(database, identity)
    return identity


def read_snapshot(database: Database, identity: str) -> Snapshot:
    """Read only a sealed snapshot and verify its canonical digest and row counts."""
    return _read_snapshot(database, identity, sealed=True)


def _read_snapshot(database: Database, identity: str, *, sealed: bool) -> Snapshot:
    snapshots = schema.VoteSnapshots
    headers = execute(
        database,
        select(snapshots).where(snapshots.id == identity),
    ).rows
    if not headers or (sealed and not headers[0]['sealed']):
        raise InputError(f'Snapshot `{identity}` is not sealed in the private store.')
    try:
        header = _SnapshotHeader.model_validate(headers[0])
        store_table = schema.VoteSnapshotStores
        stores = fetch_all(
            database,
            select(
                store_table.target,
                store_table.publication_id,
                store_table.reference,
                store_table.systems_json,
                store_table.defects_json,
                store_table.cutoff,
                store_table.excluded,
                store_table.output_generation_id,
            )
            .where(store_table.snapshot_id == identity)
            .order_by(store_table.target),
            _SnapshotStore,
        )
        case_table = schema.VoteSnapshotCases
        output_table = schema.VoteSnapshotOutputs
        vote_table = schema.VoteSnapshotVotes
        cuts: dict[Literal['production', 'private'], Cut] = {}
        for store in stores:
            cases = tuple(
                CaseFacts.model_validate_json(str(row['facts_json']))
                for row in execute(
                    database,
                    select(case_table.facts_json)
                    .where(
                        case_table.snapshot_id == identity,
                        case_table.target == store.target,
                    )
                    .order_by(case_table.case_key),
                ).rows
            )
            outputs = tuple(
                OutputFacts.model_validate_json(str(row['facts_json']))
                for row in execute(
                    database,
                    select(output_table.facts_json)
                    .where(
                        output_table.snapshot_id == identity,
                        output_table.target == store.target,
                    )
                    .order_by(output_table.output_key),
                ).rows
            )
            votes = tuple(
                Vote.model_validate_json(str(row['vote_json']))
                for row in execute(
                    database,
                    select(vote_table.vote_json)
                    .where(
                        vote_table.snapshot_id == identity,
                        vote_table.target == store.target,
                    )
                    .order_by(vote_table.vote_id),
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
