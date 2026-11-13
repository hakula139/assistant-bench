"""Publications: the reviewed cases and outputs of a family that a vote store serves."""

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    JsonValue,
    ValidationError,
    model_validator,
)

from assistant_bench.database import Database, Param
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

MAX_PARAMS = 100
"""D1's limit on the parameters one statement binds."""

_ELIGIBLE_CASES = (
    'SELECT DISTINCT a.system AS low_system, b.system AS high_system, a.case_key '
    'FROM outputs AS a JOIN outputs AS b '
    'ON b.publication_id = a.publication_id AND b.case_key = a.case_key '
    "WHERE a.publication_id = ? AND a.verdict = 'pass' AND b.verdict = 'pass' "
    'AND a.system < b.system AND a.content_digest <> b.content_digest'
)


# ── Models ──


class _Model(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


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
    """The system that anchors the round, frozen so closing uses what voters saw."""

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
    database.execute(
        'INSERT INTO publications (id, family, display_name, aspect, render, '
        'defects, reference, systems, case_count, output_count, created_at, target) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING',
        [
            publication.id,
            publication.family,
            publication.display_name,
            publication.aspect,
            publication.render,
            json.dumps(publication.defects),
            publication.reference,
            json.dumps(publication.systems, ensure_ascii=False),
            len(publication.cases),
            len(publication.outputs),
            milliseconds,
            publication.target,
        ],
    )
    _insert(
        database,
        'cases',
        (
            'publication_id',
            'case_key',
            'category',
            'split',
            'instruction',
            'checklist',
            'source',
        ),
        [
            (
                publication.id,
                case.key,
                case.category,
                case.split,
                case.instruction,
                json.dumps(case.checklist, ensure_ascii=False),
                case.source,
            )
            for case in publication.cases
        ],
    )
    _insert(
        database,
        'outputs',
        (
            'publication_id',
            'output_key',
            'case_key',
            'system',
            'fingerprint',
            'verdict',
            'errored',
            'length',
            'content_digest',
            'content',
        ),
        [
            (
                publication.id,
                output.key,
                output.case_key,
                output.system,
                output.fingerprint,
                output.verdict,
                int(output.errored),
                output.length,
                output.content_digest,
                output.content,
            )
            for output in publication.outputs
        ],
    )
    database.execute(
        'INSERT INTO publication_pair_cases '  # noqa: S608
        '(publication_id, low_system, high_system, case_keys) '
        'SELECT ?, low_system, high_system, json_group_array(case_key) '
        f'FROM ({_ELIGIBLE_CASES} ORDER BY low_system, high_system, a.case_key) '
        'GROUP BY low_system, high_system '
        'ON CONFLICT (publication_id, low_system, high_system) '
        'DO UPDATE SET case_keys = excluded.case_keys',
        [publication.id, publication.id],
    )
    if read(database, publication.id) != publication:
        raise InputError(
            f'Publication `{publication.id}` conflicts with its staged content.'
        )


def read(database: Database, publication_id: str) -> Publication:
    """Reconstruct the normalized snapshot and verify its content identity."""
    rows = database.execute(
        'SELECT id, target, family, display_name, aspect, render, defects, reference, '
        'systems, case_count, output_count FROM publications WHERE id = ?',
        [publication_id],
    ).rows
    if not rows:
        raise InputError(f'Publication `{publication_id}` does not exist.')
    row = rows[0]
    cases = database.execute(
        'SELECT case_key AS key, category, split, source, instruction, checklist '
        'FROM cases WHERE publication_id = ? ORDER BY case_key',
        [publication_id],
    ).rows
    outputs = database.execute(
        'SELECT output_key AS key, case_key, system, fingerprint, verdict, errored, '
        'length, content_digest, content FROM outputs WHERE publication_id = ? '
        'ORDER BY output_key',
        [publication_id],
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
    database.execute(
        'INSERT INTO publication_preparations '
        '(publication_id, target, collection_id, case_revision, prepared_at) '
        'VALUES (?, ?, ?, ?, ?) ON CONFLICT (publication_id) DO NOTHING',
        [
            metadata.publication_id,
            metadata.target,
            metadata.collection_id,
            metadata.case_revision,
            metadata.prepared_at.isoformat(),
        ],
    )
    if preparation(database, metadata.publication_id) != metadata:
        raise InputError(
            f'Publication `{metadata.publication_id}` has conflicting '
            'preparation metadata.'
        )


def preparation(database: Database, publication_id: str) -> Preparation | None:
    """Return its preparation, absent for an unmapped historical publication."""
    rows = database.execute(
        'SELECT publication_id, target, collection_id, case_revision, prepared_at '
        'FROM publication_preparations WHERE publication_id = ?',
        [publication_id],
    ).rows
    return Preparation.model_validate(rows[0]) if rows else None


def read_prepared(database: Database, publication_id: str) -> Publication:
    if preparation(database, publication_id) is None:
        raise InputError(f'Publication `{publication_id}` has no verified preparation.')
    return read(database, publication_id)


def activate(database: Database, publication_id: str, now: datetime) -> bool:
    """Atomically activate a complete verified publication for its family."""
    publication = read(database, publication_id)
    active = database.execute(
        'SELECT publication_id FROM active WHERE family = ?', [publication.family]
    ).rows
    if active and active[0]['publication_id'] == publication_id:
        return False
    milliseconds = int(now.timestamp() * 1000)
    activated = database.execute(
        f'WITH expected AS ({_ELIGIBLE_CASES}), stored AS ('  # noqa: S608
        'SELECT low_system, high_system, value AS case_key '
        'FROM publication_pair_cases, json_each(case_keys) '
        'WHERE publication_id = ?) '
        'INSERT INTO active (family, publication_id, activated_at) '
        'SELECT ?, ?, ? '
        'WHERE (SELECT count(*) FROM cases WHERE publication_id = ?) = ? '
        'AND (SELECT count(*) FROM outputs WHERE publication_id = ?) = ? '
        'AND NOT EXISTS (SELECT * FROM expected EXCEPT SELECT * FROM stored) '
        'AND NOT EXISTS (SELECT * FROM stored EXCEPT SELECT * FROM expected) '
        'ON CONFLICT (family) DO UPDATE SET '
        'publication_id = excluded.publication_id, '
        'activated_at = excluded.activated_at',
        [
            publication.id,
            publication.id,
            publication.family,
            publication.id,
            milliseconds,
            publication.id,
            len(publication.cases),
            publication.id,
            len(publication.outputs),
        ],
    )
    if activated.changes != 1:
        raise InputError(
            f'Publication `{publication.id}` is not completely staged, so it was '
            'not activated. Publish the same ID again.'
        )
    return True


def _insert(
    database: Database,
    table: str,
    columns: Sequence[str],
    rows: Sequence[Sequence[Param]],
) -> None:
    """Insert rows missing from the table in statements within D1's parameter limit."""
    per_statement = MAX_PARAMS // len(columns)
    placeholders = f'({", ".join("?" * len(columns))})'
    for start in range(0, len(rows), per_statement):
        chunk = rows[start : start + per_statement]
        database.execute(
            # `table` and `columns` are constants from callers.
            f'INSERT INTO {table} ({", ".join(columns)}) VALUES '  # noqa: S608
            f'{", ".join([placeholders] * len(chunk))} ON CONFLICT DO NOTHING',
            [value for row in chunk for value in row],
        )


def _digest(value: JsonValue) -> str:
    canonical = json.dumps(
        value, sort_keys=True, separators=(',', ':'), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
