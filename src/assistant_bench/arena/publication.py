"""Publications: the reviewed cases and outputs of a family that a vote store serves."""

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    JsonValue,
    ValidationError,
    model_validator,
)
from sqlalchemy import (
    CTE,
    Select,
    and_,
    except_,
    exists,
    func,
    literal,
    select,
    union_all,
)
from sqlalchemy.dialects import sqlite
from sqlalchemy.orm import aliased

from assistant_bench import schema
from assistant_bench.database import (
    Database,
    execute,
    fetch_one,
    fetch_scalar,
    insert_missing,
)
from assistant_bench.families import (
    Case,
    CaseSource,
    Category,
    Defect,
    Family,
    FamilyName,
    Gate,
    VotesSettings,
)
from assistant_bench.html import extract_html
from assistant_bench.messages import InputError, code, error_lines
from assistant_bench.outputs import (
    Output,
    Verdict,
    refuse_pool_conflicts,
    system_fingerprints,
)
from assistant_bench.scoring.length import text_length


PUBLIC_SOURCES: tuple[CaseSource, ...] = ('public', 'synthetic')
type VotingTarget = Literal['production', 'private']
type CaseSplit = Literal['public', 'held-out', 'private']

CASE_SPLITS: dict[CaseSource, CaseSplit] = {
    'synthetic': 'public',
    'public': 'public',
    'transcript': 'private',
    'held-out': 'held-out',
}

# ── Models ──


class _Model(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class _FingerprintConflict(_Model):
    system: str
    recorded: str
    candidate: str


class CaseFacts(_Model):
    key: str
    category: Category
    split: CaseSplit


class PublishedCase(CaseFacts):
    source: CaseSource
    instruction: str
    checklist: tuple[str, ...]


class OutputFacts(_Model):
    key: str
    case_key: str
    system: str
    fingerprint: str
    verdict: Verdict
    errored: bool
    length: int | None
    content_digest: str


class PublishedOutput(OutputFacts):
    content: str | None
    """The text, diff, or HTML document, present exactly when the output passed."""

    @model_validator(mode='after')
    def _content_when_passing(self) -> Self:
        if (self.verdict == 'pass') != (self.content is not None):
            raise ValueError('an output carries content exactly when it passed')
        return self


class Inventory(_Model):
    """A publication's identities and verdicts without its content."""

    id: str
    family: FamilyName
    reference: str
    systems: dict[str, str]
    """Each system's display name, in the family's order."""

    defects: tuple[Defect, ...]
    cases: tuple[CaseFacts, ...]
    outputs: tuple[OutputFacts, ...]


class Publication(_Model):
    """An immutable snapshot of a family's cases and outputs, named by its digest."""

    id: str
    target: VotingTarget
    family: FamilyName
    display_name: str
    aspect: str | None
    render: Literal['text', 'diff', 'html']
    defects: tuple[Defect, ...]
    reference: str
    """Reference for expected net points, frozen so closing uses what voters saw."""

    systems: dict[str, str]
    """Each published system's and the reference's display name, in family order."""

    cases: tuple[PublishedCase, ...]
    outputs: tuple[PublishedOutput, ...]

    @model_validator(mode='after')
    def _html_documents(self) -> Self:
        if self.render == 'html':
            for output in self.outputs:
                if output.length is not None:
                    raise ValueError('HTML outputs have no text length')
                if (
                    output.content is not None
                    and extract_html(output.content) != output.content
                ):
                    raise ValueError(
                        'a passing HTML output must be an eligible document'
                    )
        return self

    def identity(self) -> str:
        """Return the digest of everything but the recorded ID."""
        return _digest(self.model_dump(mode='json', exclude={'id'}))


class Preparation(_Model):
    publication_id: str
    target: VotingTarget
    collection_id: str
    case_revision: str | None
    prepared_at: AwareDatetime


# ── Preparation ──


class NoPairsError(InputError):
    """The selected publication has no comparison two systems can form."""


def voting_target(family: Family, case: Case) -> VotingTarget:
    """Select the sole vote store allowed to hold this case's content."""
    assert isinstance(family.settings, VotesSettings)
    if family.settings.public_voting and case.source in PUBLIC_SOURCES:
        return 'production'
    return 'private'


def case_key(case: Case) -> str:
    """Digest what the voter reads, so an unchanged case keeps its key."""
    checklist: list[JsonValue] = list(case.checklist)
    return _digest({'instruction': case.instruction, 'checklist': checklist})


def output_key(family: str, output: Output) -> str:
    """Digest the trial that wrote the output, so republishing keeps its votes."""
    return _digest({'family': family, 'job': output.job, 'trial': output.trial})


def prepare(
    family: Family,
    cases: Sequence[Case],
    outputs: Sequence[Output],
    system_names: Mapping[str, str],
    *,
    target: VotingTarget,
) -> Publication:
    """Build the target's publication of the family's listed systems.

    Raises
    ------
    InputError
        The family is not scored by votes or sets no `public_voting`, two cases
        read the same, a kept output lacks a fingerprint, a system has several or
        pools several models, or no case can form a pair.
    """
    settings = family.settings
    if not isinstance(settings, VotesSettings):
        raise InputError(f'Family `{family.name}` is not scored by votes.')

    if target == 'production' and not settings.public_voting:
        raise InputError(
            f'Family `{family.name}` sets no `public_voting`, so production votes '
            'on none of its cases. Set `public_voting = true` in '
            f'`families/{family.name}/family.toml` once its committed cases may '
            'be shown to the public.'
        )

    all_keys = {case.id: case_key(case) for case in cases}
    _refuse_repeated_cases(all_keys)
    keys = {
        case.id: all_keys[case.id]
        for case in cases
        if voting_target(family, case) == target
    }

    kept = [
        output
        for output in outputs
        if output.case in keys and output.system in settings.systems
    ]
    if unknown := sorted({o.system for o in kept if o.fingerprint is None}):
        raise InputError(
            f'Family `{family.name}`: outputs of {code(unknown)} lack a fingerprint, '
            'so they cannot be published. Rerun them with `uv run bench run`.'
        )

    fingerprints = system_fingerprints(kept)
    refuse_pool_conflicts(family, kept)
    published = sorted(
        (
            _published_output(family.name, keys[o.case], o, settings.render)
            for o in kept
        ),
        key=lambda output: output.key,
    )
    if not _pairable(published):
        raise NoPairsError(
            f'Family `{family.name}` has no {target} case on which two systems '
            'have different passing outputs, so no pair can be voted on.'
        )

    used = {output.case_key for output in published}
    draft = Publication(
        id='',
        target=target,
        family=family.name,
        display_name=settings.display_name,
        aspect=settings.aspect,
        render=settings.render,
        defects=tuple(settings.defects),
        reference=settings.reference,
        systems={
            system: system_names[system]
            for system in settings.systems
            if system in fingerprints or system == settings.reference
        },
        cases=tuple(
            sorted(
                (
                    PublishedCase(
                        key=keys[case.id],
                        category=case.category,
                        split=CASE_SPLITS[case.source],
                        source=case.source,
                        instruction=case.instruction,
                        checklist=tuple(case.checklist),
                    )
                    for case in cases
                    if keys.get(case.id) in used
                ),
                key=lambda case: case.key,
            )
        ),
        outputs=tuple(published),
    )
    return draft.model_copy(update={'id': draft.identity()})


def _refuse_repeated_cases(keys: Mapping[str, str]) -> None:
    by_key: defaultdict[str, list[str]] = defaultdict(list)
    for case, key in keys.items():
        by_key[key].append(case)

    if repeated := [cases for cases in by_key.values() if len(cases) > 1]:
        raise InputError(
            ' '.join(
                f'Cases {code(cases)} share an instruction and checklist, so a '
                'voter could not tell them apart.'
                for cases in repeated
            )
        )


def _published_output(
    family: str, case: str, output: Output, render: Literal['text', 'diff', 'html']
) -> PublishedOutput:
    assert output.fingerprint is not None
    verdict = _verdict(output.gate_failures)
    text = output.text or ''
    if render == 'html' and verdict == 'pass':
        document = extract_html(text)
        if document is None or document != text:
            raise InputError(
                f'Family `{family}` has a passing HTML output that is not a '
                'collected eligible document. Collect its trials again.'
            )
    return PublishedOutput(
        key=output_key(family, output),
        case_key=case,
        system=output.system,
        fingerprint=output.fingerprint,
        verdict=verdict,
        errored=output.error is not None,
        length=None if render == 'html' else text_length(text),
        content_digest=_digest(text),
        content=output.text if verdict == 'pass' else None,
    )


def _verdict(gate_failures: Sequence[Gate] | None) -> Verdict:
    if gate_failures is None:
        return 'ungraded'
    return 'fail' if gate_failures else 'pass'


def _pairable(outputs: Iterable[OutputFacts]) -> bool:
    """Whether some case has passing outputs of two systems that differ."""
    passing: defaultdict[str, set[tuple[str, str]]] = defaultdict(set)
    for output in outputs:
        if output.verdict == 'pass':
            passing[output.case_key].add((output.system, output.content_digest))

    return any(
        a_system != b_system and a_content != b_content
        for samples in passing.values()
        for a_system, a_content in samples
        for b_system, b_content in samples
    )


# ── Review ──


def listing(publication: Publication, outputs: Sequence[Output]) -> str:
    """Render the prepared snapshot and its generation's gate failures."""
    gates = {
        output_key(publication.family, output): output.gate_failures or []
        for output in outputs
    }
    return _listing(publication, gates)


def _listing(publication: Publication, gates: Mapping[str, Sequence[Gate]]) -> str:
    lines = [
        f'# {publication.display_name} (`{publication.family}`)',
        '',
        f'Publication `{publication.id}`: {len(publication.cases)} cases and '
        f'{len(publication.outputs)} outputs.',
    ]
    by_case: defaultdict[str, list[PublishedOutput]] = defaultdict(list)
    for output in publication.outputs:
        by_case[output.case_key].append(output)

    for case in publication.cases:
        lines += ['', f'## Case `{case.key[:12]}` ({case.category})', '']
        lines += [_fenced(case.instruction, 'text'), '']
        lines += [f'- {item}' for item in case.checklist] or ['No checklist.']
        for output in sorted(by_case[case.key], key=lambda row: row.system):
            name = publication.systems[output.system]
            heading = f'### {name} (`{output.system}`): {output.verdict}'
            lines += ['', heading, '']
            if output.content is not None:
                lines.append(_fenced(output.content, publication.render))
            elif failed := gates.get(output.key):
                lines.append(f'Failed {code(failed)}.')
            else:
                lines.append('No verdict.')

    return '\n'.join(lines) + '\n'


def _fenced(text: str, language: str) -> str:
    """Fence `text` with more backticks than any run it holds."""
    longest = max((len(run) for run in re.findall('`+', text)), default=0)
    fence = '`' * max(3, longest + 1)
    return f'{fence}{language}\n{text.rstrip()}\n{fence}'


# ── Publishing ──


def _eligible_cases(publication_id: str) -> Select[str, str, str]:
    left = aliased(schema.Outputs, name='a')
    right = aliased(schema.Outputs, name='b')
    return (
        select(
            left.system.label('low_system'),
            right.system.label('high_system'),
            left.case_key,
        )
        .distinct()
        .join(
            right,
            and_(
                right.publication_id == left.publication_id,
                right.case_key == left.case_key,
            ),
        )
        .where(
            left.publication_id == publication_id,
            left.verdict == 'pass',
            right.verdict == 'pass',
            left.system < right.system,
            left.content_digest != right.content_digest,
        )
    )


def _ballot_outputs(family: FamilyName) -> Select[str, str, str, str]:
    votes = aliased(schema.Votes, name='v')
    left = aliased(schema.Outputs, name='a')
    right = aliased(schema.Outputs, name='b')
    return (
        select(
            left.system,
            left.fingerprint,
            right.system.label('peer'),
            right.fingerprint.label('peer_fingerprint'),
        )
        .select_from(votes)
        .join(
            left,
            and_(
                left.publication_id == votes.publication_id,
                left.output_key == votes.a_output,
            ),
        )
        .join(
            right,
            and_(
                right.publication_id == votes.publication_id,
                right.output_key == votes.b_output,
            ),
        )
        .where(
            votes.family == family,
            left.case_key == votes.case_key,
            right.case_key == votes.case_key,
            left.system == votes.a_system,
            right.system == votes.b_system,
            left.system != right.system,
            left.verdict == 'pass',
            right.verdict == 'pass',
            left.content_digest != right.content_digest,
        )
    )


def _fingerprint_conflicts(
    ballots: CTE, fingerprints_json: str
) -> Select[Any, Any, Any]:
    prior = union_all(
        select(ballots.c.system, ballots.c.fingerprint),
        select(ballots.c.peer, ballots.c.peer_fingerprint),
    ).subquery('prior')
    incoming = (
        func.json_each(fingerprints_json).table_valued('key', 'value').alias('incoming')
    )
    return (
        select(
            prior.c.system,
            prior.c.fingerprint.label('recorded'),
            incoming.c.value.label('candidate'),
        )
        .select_from(prior)
        .join(incoming, incoming.c.key == prior.c.system)
        .where(incoming.c.value != prior.c.fingerprint)
    )


def stage(
    database: Database,
    publication: Publication,
    now: datetime,
) -> None:
    """Insert and verify an immutable publication without activating it."""
    if publication.identity() != publication.id:
        raise InputError(f'Publication `{publication.id}` has an invalid identity.')
    if publication.target == 'production' and (
        private := [c for c in publication.cases if c.source not in PUBLIC_SOURCES]
    ):
        raise InputError(
            f'Publication `{publication.id}` holds {len(private)} cases whose source '
            'is neither `public` nor `synthetic`, so it cannot enter production.'
        )

    milliseconds = int(now.timestamp() * 1000)
    execute(
        database,
        sqlite.insert(schema.Publications)
        .values(
            id=publication.id,
            family=publication.family,
            display_name=publication.display_name,
            aspect=publication.aspect,
            render=publication.render,
            defects=json.dumps(publication.defects),
            reference=publication.reference,
            systems=json.dumps(publication.systems, ensure_ascii=False),
            case_count=len(publication.cases),
            output_count=len(publication.outputs),
            created_at=milliseconds,
            target=publication.target,
        )
        .on_conflict_do_nothing(index_elements=[schema.Publications.id]),
    )

    insert_missing(
        database,
        schema.Cases,
        (
            {
                'publication_id': publication.id,
                'case_key': case.key,
                'category': case.category,
                'split': case.split,
                'instruction': case.instruction,
                'checklist': json.dumps(case.checklist, ensure_ascii=False),
                'source': case.source,
            }
            for case in publication.cases
        ),
    )

    insert_missing(
        database,
        schema.Outputs,
        (
            {
                'publication_id': publication.id,
                'output_key': output.key,
                'case_key': output.case_key,
                'system': output.system,
                'fingerprint': output.fingerprint,
                'verdict': output.verdict,
                'errored': int(output.errored),
                'length': output.length,
                'content_digest': output.content_digest,
                'content': output.content,
            }
            for output in publication.outputs
        ),
    )

    eligible = _eligible_cases(publication.id)
    ordered = eligible.order_by(
        eligible.selected_columns.low_system,
        eligible.selected_columns.high_system,
        eligible.selected_columns.case_key,
    ).subquery()
    pair_cases = sqlite.insert(schema.PublicationPairCases)
    execute(
        database,
        pair_cases.from_select(
            [
                schema.PublicationPairCases.publication_id,
                schema.PublicationPairCases.low_system,
                schema.PublicationPairCases.high_system,
                schema.PublicationPairCases.case_keys,
            ],
            select(
                literal(publication.id),
                ordered.c.low_system,
                ordered.c.high_system,
                func.json_group_array(ordered.c.case_key),
            ).group_by(ordered.c.low_system, ordered.c.high_system),
        ).on_conflict_do_update(
            index_elements=[
                schema.PublicationPairCases.publication_id,
                schema.PublicationPairCases.low_system,
                schema.PublicationPairCases.high_system,
            ],
            set_={'case_keys': pair_cases.excluded.case_keys},
        ),
    )
    if read(database, publication.id) != publication:
        raise InputError(
            f'Publication `{publication.id}` conflicts with its staged content.'
        )


def read(database: Database, publication_id: str) -> Publication:
    """Reconstruct the normalized snapshot and verify its content identity."""
    publications = schema.Publications
    rows = execute(
        database,
        select(
            publications.id,
            publications.target,
            publications.family,
            publications.display_name,
            publications.aspect,
            publications.render,
            publications.defects,
            publications.reference,
            publications.systems,
            publications.case_count,
            publications.output_count,
        ).where(publications.id == publication_id),
    ).rows
    if not rows:
        raise InputError(f'Publication `{publication_id}` does not exist.')
    row = rows[0]

    cases_table = schema.Cases
    cases = execute(
        database,
        select(
            cases_table.case_key.label('key'),
            cases_table.category,
            cases_table.split,
            cases_table.source,
            cases_table.instruction,
            cases_table.checklist,
        )
        .where(cases_table.publication_id == publication_id)
        .order_by(cases_table.case_key),
    ).rows

    outputs_table = schema.Outputs
    outputs = execute(
        database,
        select(
            outputs_table.output_key.label('key'),
            outputs_table.case_key,
            outputs_table.system,
            outputs_table.fingerprint,
            outputs_table.verdict,
            outputs_table.errored,
            outputs_table.length,
            outputs_table.content_digest,
            outputs_table.content,
        )
        .where(outputs_table.publication_id == publication_id)
        .order_by(outputs_table.output_key),
    ).rows
    try:
        publication = Publication.model_validate(
            {
                **{
                    key: value
                    for key, value in row.items()
                    if key not in ('case_count', 'output_count')
                },
                'defects': json.loads(str(row['defects'])),
                'systems': json.loads(str(row['systems'])),
                'cases': [
                    {**case, 'checklist': json.loads(str(case['checklist']))}
                    for case in cases
                ],
                'outputs': outputs,
            }
        )
    except ValidationError as error:
        raise InputError(
            error_lines(error, f'Publication `{publication_id}`')
        ) from error
    if (len(cases), len(outputs)) != (
        row['case_count'],
        row['output_count'],
    ) or publication.identity() != publication_id:
        raise InputError(
            f'Publication `{publication_id}` does not match its immutable snapshot.'
        )
    return publication


def record_preparation(database: Database, metadata: Preparation) -> None:
    """Mark a verified staged snapshot ready for review and publication."""
    publication = read(database, metadata.publication_id)
    if publication.target != metadata.target:
        raise InputError('A preparation must match its publication target.')
    execute(
        database,
        sqlite.insert(schema.PublicationPreparations)
        .values(
            publication_id=metadata.publication_id,
            target=metadata.target,
            collection_id=metadata.collection_id,
            case_revision=metadata.case_revision,
            prepared_at=metadata.prepared_at.isoformat(),
        )
        .on_conflict_do_nothing(
            index_elements=[schema.PublicationPreparations.publication_id]
        ),
    )
    if preparation(database, metadata.publication_id) != metadata:
        raise InputError(
            f'Publication `{metadata.publication_id}` has conflicting '
            'preparation metadata.'
        )


def preparation(database: Database, publication_id: str) -> Preparation | None:
    """Return its preparation, absent for an unmapped historical publication."""
    preparations = schema.PublicationPreparations
    return fetch_one(
        database,
        select(preparations).where(preparations.publication_id == publication_id),
        Preparation,
    )


def read_prepared(database: Database, publication_id: str) -> Publication:
    if preparation(database, publication_id) is None:
        raise InputError(f'Publication `{publication_id}` has no verified preparation.')
    return read(database, publication_id)


def refuse_fingerprint_conflicts(
    database: Database,
    publication: Publication,
    store: VotingTarget,
) -> None:
    """Refuse evidence that could pool different fingerprints under one system."""
    ballots = _ballot_outputs(publication.family).cte('ballots')
    conflicts = _fingerprint_conflicts(ballots, _fingerprint_json(publication))
    conflict = fetch_one(
        database,
        conflicts.order_by(
            conflicts.selected_columns.system,
            conflicts.selected_columns.recorded,
        ).limit(1),
        _FingerprintConflict,
    )
    if conflict is not None:
        raise InputError(
            f'Family `{publication.family}` has voting evidence in `{store}` for '
            f'system `{conflict.system}` under fingerprint '
            f'`{conflict.recorded}`. '
            f'This publication uses `{conflict.candidate}`. Give the changed '
            'configuration a distinct system ID and display name, update the family '
            'and its reference as needed, then rerun, collect and prepare again. '
            'See `docs/guide/authoring.md#systems`.'
        )


def _fingerprint_json(publication: Publication) -> str:
    return json.dumps(
        {output.system: output.fingerprint for output in publication.outputs}
    )


def activate(
    database: Database,
    publication_id: str,
    now: datetime,
    *,
    preserve_ballots: bool = True,
) -> bool:
    """Atomically activate a complete verified publication for its family."""
    publication = read(database, publication_id)
    active_publication = fetch_scalar(
        database,
        select(schema.Active.publication_id).where(
            schema.Active.family == publication.family
        ),
        str,
    )
    if active_publication == publication_id:
        return False

    milliseconds = int(now.timestamp() * 1000)
    expected = _eligible_cases(publication.id).cte('expected')
    pair_cases = schema.PublicationPairCases
    each_case = func.json_each(pair_cases.case_keys).table_valued('value')
    stored = (
        select(
            pair_cases.low_system,
            pair_cases.high_system,
            each_case.c.value.label('case_key'),
        )
        .select_from(pair_cases, each_case)
        .where(pair_cases.publication_id == publication.id)
        .cte('stored')
    )
    conditions = [
        select(func.count())
        .select_from(schema.Cases)
        .where(schema.Cases.publication_id == publication.id)
        .scalar_subquery()
        == len(publication.cases),
        select(func.count())
        .select_from(schema.Outputs)
        .where(schema.Outputs.publication_id == publication.id)
        .scalar_subquery()
        == len(publication.outputs),
        ~exists(except_(select(expected), select(stored))),
        ~exists(except_(select(stored), select(expected))),
    ]
    if preserve_ballots:
        ballots = _ballot_outputs(publication.family).cte('ballots')
        conditions.append(
            ~exists(_fingerprint_conflicts(ballots, _fingerprint_json(publication)))
        )
    active_insert = sqlite.insert(schema.Active)
    activated_id = fetch_scalar(
        database,
        active_insert.from_select(
            [
                schema.Active.family,
                schema.Active.publication_id,
                schema.Active.activated_at,
            ],
            select(
                literal(publication.family),
                literal(publication.id),
                literal(milliseconds),
            ).where(*conditions),
        )
        .on_conflict_do_update(
            index_elements=[schema.Active.family],
            set_={
                'publication_id': active_insert.excluded.publication_id,
                'activated_at': active_insert.excluded.activated_at,
            },
        )
        .returning(schema.Active.publication_id),
        str,
    )
    if activated_id != publication.id:
        if preserve_ballots:
            refuse_fingerprint_conflicts(database, publication, publication.target)
        raise InputError(
            f'Publication `{publication.id}` is not completely staged, so it was '
            'not activated. Publish the same ID again.'
        )
    return True


def _digest(value: JsonValue) -> str:
    canonical = json.dumps(
        value, sort_keys=True, separators=(',', ':'), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
