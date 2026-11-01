"""A store's vote cuts and moderation, the snapshots that save cuts, and rounds."""

import json
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import datetime
from itertools import combinations, product
from pathlib import Path
from statistics import median
from typing import Literal, Protocol, get_args

from pydantic import AwareDatetime, BaseModel, ConfigDict, Json

from assistant_bench.arena.d1 import Database, Param, Row
from assistant_bench.arena.publication import (
    CaseFacts,
    Inventory,
    OutputFacts,
    output_key,
)
from assistant_bench.families import Choice, Defect, Family, FamilyName, VotesSettings
from assistant_bench.messages import code
from assistant_bench.outputs import Output
from assistant_bench.paths import data_dir, write_atomically
from assistant_bench.report import (
    AnalysisInput,
    Cutoff,
    Cutoffs,
    HumanEvidence,
    OutputMeasurement,
)
from assistant_bench.scoring import ranking
from assistant_bench.scoring.weighting import actor_weights


type Store = Literal['production', 'local']

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


class Snapshot(_Model):
    """The cuts one `bench close` took, saved under `votes/<family>/`."""

    family: FamilyName
    taken_at: AwareDatetime
    production: Cut | None
    local: Cut | None


# ── Reading a Store ──


class _ActiveRow(BaseModel):
    id: str
    reference: str
    systems: Json[dict[str, str]]
    defects: Json[tuple[Defect, ...]]


class _OutputRow(BaseModel):
    output_key: str
    case_key: str
    system: str
    fingerprint: str
    verdict: Literal['pass', 'fail', 'ungraded']
    errored: bool
    length: int
    content_digest: str


class _VoteRow(BaseModel):
    id: int
    actor_id: str
    case_key: str
    a_output: str
    b_output: str
    a_system: str
    b_system: str
    choice: Choice
    defects: Json[TaggedDefects]
    elapsed_ms: int


def read_cut(database: Database, family: str, now: datetime) -> Cut | None:
    """Read the family's active publication and the votes no undo can change.

    Returns None when the store has no active publication for the family.
    """
    active = database.execute(
        'SELECT publications.id, publications.reference, publications.systems, '
        'publications.defects FROM active JOIN publications '
        'ON publications.id = active.publication_id WHERE active.family = ?',
        [family],
    ).rows
    if not active:
        return None

    publication = _ActiveRow.model_validate(active[0])
    cases = database.execute(
        'SELECT case_key AS key, category FROM cases '
        'WHERE publication_id = ? ORDER BY case_key',
        [publication.id],
    ).rows
    outputs = database.execute(
        'SELECT output_key, case_key, system, fingerprint, verdict, errored, length, '
        'content_digest FROM outputs WHERE publication_id = ? ORDER BY output_key',
        [publication.id],
    ).rows
    inventory = Inventory(
        id=publication.id,
        family=family,
        reference=publication.reference,
        systems=publication.systems,
        defects=publication.defects,
        cases=tuple(CaseFacts.model_validate(row) for row in cases),
        outputs=tuple(
            OutputFacts(
                key=row.output_key,
                **row.model_dump(exclude={'output_key'}),
            )
            for row in map(_OutputRow.model_validate, outputs)
        ),
    )

    window_start = int(now.timestamp() * 1000) - UNDO_WINDOW_MS
    cutoff = database.execute(
        'SELECT coalesce(max(id), 0) AS cutoff FROM votes '
        'WHERE family = ? AND recorded_at <= ?',
        [family, window_start],
    ).rows[0]['cutoff']
    assert isinstance(cutoff, int)
    if cutoff == 0:
        return Cut(cutoff=0, inventory=inventory, votes=(), excluded=0)

    rows = _paged(
        database,
        _VoteRow.model_validate,
        'id, actor_id, case_key, a_output, b_output, a_system, b_system, choice, '
        'defects, elapsed_ms',
        'family = ? AND exclusion IS NULL AND id <= ?',
        [family, cutoff],
    )
    votes = [
        Vote(actor=row.actor_id, **row.model_dump(exclude={'actor_id'})) for row in rows
    ]
    excluded = database.execute(
        'SELECT count(*) AS excluded FROM votes WHERE family = ? '
        'AND exclusion IS NOT NULL AND id <= ?',
        [family, cutoff],
    ).rows[0]['excluded']
    assert isinstance(excluded, int)
    return Cut(
        cutoff=cutoff, inventory=inventory, votes=tuple(votes), excluded=excluded
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


class FamilyStatus(BaseModel):
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
    """Refuse an exclusion reason that is blank.

    Raises
    ------
    ValueError
        The reason is blank.
    """
    if not reason.strip():
        raise ValueError('Give a reason for the exclusion.')


def exclude(database: Database, family: str, selector: Selector, reason: str) -> int:
    """Exclude the selected votes for `reason`, returning how many changed.

    A vote excluded earlier keeps its first reason.

    Raises
    ------
    ValueError
        The reason is blank.
    """
    check_reason(reason)
    condition, param = selector.condition()
    return database.execute(
        'UPDATE votes SET exclusion = ? WHERE family = ? AND exclusion IS NULL '
        f'AND {condition}',
        [reason, family, param],
    ).changes


def restore(database: Database, family: str, selector: Selector) -> int:
    """Count the selected excluded votes again, returning how many changed."""
    condition, param = selector.condition()
    return database.execute(
        'UPDATE votes SET exclusion = NULL WHERE family = ? AND exclusion IS NOT NULL '
        f'AND {condition}',
        [family, param],
    ).changes


# ── Snapshots ──


def write_snapshot(snapshot: Snapshot) -> Path:
    path = (
        data_dir()
        / 'votes'
        / snapshot.family
        / f'{snapshot.taken_at:%Y-%m-%dT%H-%M-%SZ}.json'
    )
    write_atomically(path, snapshot.model_dump_json(indent=2) + '\n')
    return path


def read_snapshot(path: Path) -> Snapshot:
    return Snapshot.model_validate_json(path.read_bytes())


# ── Closing ──


def voting_input(
    snapshot: Snapshot, family: Family, outputs: Sequence[Output]
) -> AnalysisInput:
    """Build the round's input from the snapshot's cuts and the local outputs.

    A vote counts when both its outputs passed in its store's publication, on the
    vote's case under the vote's systems, with different content. Latency, tokens
    and cost come from `outputs`, matched by output key, and an output missing
    from them measures as null.

    Raises
    ------
    ValueError
        The family is not scored by votes, the snapshot belongs to another
        family or holds no cut, or two cuts name a system or the reference
        differently.
    """
    settings = family.settings
    if not isinstance(settings, VotesSettings):
        raise ValueError(f'Family `{family.name}` is not scored by votes.')
    if snapshot.family != family.name:
        raise ValueError(
            f'The snapshot belongs to family `{snapshot.family}`, not `{family.name}`.'
        )

    cuts = stored_cuts(snapshot)
    if not cuts:
        raise ValueError('The snapshot holds no cut from any store.')

    counted: list[tuple[Store, Vote]] = []
    dropped = 0
    for store, cut in cuts:
        published = {output.key: output for output in cut.inventory.outputs}
        for vote in cut.votes:
            if _counts(vote, published):
                counted.append((store, vote))
            else:
                dropped += 1
        dropped += cut.excluded

    lengths = {
        output.key: output.length for _, cut in cuts for output in cut.inventory.outputs
    }
    actors = [f'{store}:{vote.actor}' for store, vote in counted]
    ordinary = tuple(
        HumanEvidence(
            receipt=f'{store}:{vote.id}',
            actor=actor,
            choice=vote.choice,
            defects_a=tuple(sorted(vote.defects.a)),
            defects_b=tuple(sorted(vote.defects.b)),
            length_a=lengths[vote.a_output],
            length_b=lengths[vote.b_output],
            comparison=ranking.Comparison(
                vote.a_system,
                vote.b_system,
                ranking.LEFT_SCORE[vote.choice],
                vote.case_key,
                weight,
            ),
        )
        for (store, vote), actor, weight in zip(
            counted, actors, actor_weights(actors), strict=True
        )
    )

    inventory = [output for _, cut in cuts for output in cut.inventory.outputs]
    measured = {output_key(family.name, output): output for output in outputs}
    return AnalysisInput(
        family=family.name,
        reference=reference(snapshot),
        systems=tuple(system_names(snapshot)),
        defects=cuts[0][1].inventory.defects,
        cutoffs=Cutoffs(
            production=_cutoff(snapshot.production), local=_cutoff(snapshot.local)
        ),
        declared_categories=tuple(settings.categories),
        categories=tuple(
            (case.key, case.category) for _, cut in cuts for case in cut.inventory.cases
        ),
        ordinary=ordinary,
        settlements=_settlements(inventory),
        outputs=tuple(_measurement(row, measured.get(row.key)) for row in inventory),
        dropped=dropped,
    )


def stored_cuts(snapshot: Snapshot) -> list[tuple[Store, Cut]]:
    """Return the snapshot's cuts with their stores, production first."""
    stores: tuple[tuple[Store, Cut | None], ...] = (
        ('production', snapshot.production),
        ('local', snapshot.local),
    )
    return [(store, cut) for store, cut in stores if cut is not None]


def system_names(snapshot: Snapshot) -> dict[str, str]:
    """Map every published system to its display name, in publication order.

    Raises
    ------
    ValueError
        Two cuts give a system different display names.
    """
    names: dict[str, str] = {}
    for _, cut in stored_cuts(snapshot):
        for system, name in cut.inventory.systems.items():
            if names.setdefault(system, name) != name:
                raise ValueError(
                    f'The stores name system `{system}` both `{names[system]}` '
                    f'and `{name}`.'
                )
    return names


def reference(snapshot: Snapshot) -> str:
    """Return the reference the cuts' publications froze.

    Raises
    ------
    ValueError
        The cuts froze different references, or the snapshot holds none.
    """
    references = {cut.inventory.reference for _, cut in stored_cuts(snapshot)}
    if len(references) != 1:
        raise ValueError(
            f'The stores anchor on {code(sorted(references))}, so the round has no '
            'single reference.'
        )
    return references.pop()


def fingerprints(snapshot: Snapshot) -> dict[str, str]:
    return {
        output.system: output.fingerprint
        for _, cut in stored_cuts(snapshot)
        for output in cut.inventory.outputs
    }


def _cutoff(cut: Cut | None) -> Cutoff | None:
    return None if cut is None else Cutoff(id=cut.cutoff, votes=len(cut.votes))


def _counts(vote: Vote, published: dict[str, OutputFacts]) -> bool:
    a, b = published.get(vote.a_output), published.get(vote.b_output)
    return (
        a is not None
        and b is not None
        and a.case_key == b.case_key == vote.case_key
        and (a.system, b.system) == (vote.a_system, vote.b_system)
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
