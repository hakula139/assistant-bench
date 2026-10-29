"""Publications: the reviewed cases and outputs of a family that a vote store serves."""

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, JsonValue, model_validator

from assistant_bench.arena.d1 import Database, Param
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
from assistant_bench.messages import code
from assistant_bench.outputs import Output, system_fingerprints
from assistant_bench.paths import data_dir, write_atomically
from assistant_bench.scoring.length import text_length


type VotingTarget = Literal['production', 'local']
type Verdict = Literal['pass', 'fail', 'ungraded']

PUBLIC_SOURCES: tuple[CaseSource, ...] = ('public', 'synthetic')
MAX_PARAMS = 100
"""D1's limit on the parameters one statement binds."""


# ── Models ──


class _Model(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class CaseFacts(_Model):
    key: str
    category: Category


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
    length: int
    content_digest: str


class PublishedOutput(OutputFacts):
    content: str | None
    """The rendered text or diff, present exactly when the output passed."""

    @model_validator(mode='after')
    def _content_when_passing(self) -> Self:
        if (self.verdict == 'pass') != (self.content is not None):
            raise ValueError('an output carries content exactly when it passed')
        return self


class Inventory(_Model):
    """A publication's identities and verdicts without its content."""

    id: str
    family: FamilyName
    systems: dict[str, str]
    """Each system's display name, in the family's order."""

    defects: tuple[Defect, ...]
    cases: tuple[CaseFacts, ...]
    outputs: tuple[OutputFacts, ...]


class Publication(_Model):
    """An immutable snapshot of a family's cases and outputs, named by its digest."""

    id: str
    family: FamilyName
    display_name: str
    aspect: str | None
    render: Literal['text', 'diff']
    defects: tuple[Defect, ...]
    systems: dict[str, str]
    """Each system's display name, in the family's order."""

    cases: tuple[PublishedCase, ...]
    outputs: tuple[PublishedOutput, ...]

    def identity(self) -> str:
        """Return the digest of everything but the recorded ID."""
        return _digest(self.model_dump(mode='json', exclude={'id'}))


# ── Preparation ──


def voting_target(family: Family, case: Case) -> VotingTarget:
    """Return the one store that votes on the case.

    Production votes only on public and synthetic cases of a family that sets
    `public_voting`, and every other case is voted locally.
    """
    settings = family.settings
    if (
        isinstance(settings, VotesSettings)
        and settings.public_voting
        and case.source in PUBLIC_SOURCES
    ):
        return 'production'
    return 'local'


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
    target: VotingTarget,
) -> Publication:
    """Build the publication of the family's listed systems on the target's cases.

    Raises
    ------
    ValueError
        The family is not scored by votes, it sets no `public_voting` for a
        production publication, two cases read the same, a kept output lacks a
        fingerprint, a system has several, or no case can form a pair.
    """
    settings = family.settings
    if not isinstance(settings, VotesSettings):
        raise ValueError(f'Family `{family.name}` is not scored by votes.')

    if target == 'production' and not settings.public_voting:
        raise ValueError(
            f'Family `{family.name}` sets no `public_voting`, so production votes '
            'on none of its cases. Set `public_voting = true` in '
            f'`families/{family.name}/family.toml` once its committed cases may '
            'be shown to the public.'
        )

    keys = {
        case.id: case_key(case)
        for case in cases
        if voting_target(family, case) == target
    }
    _refuse_repeated_cases(keys)

    kept = [
        output
        for output in outputs
        if output.case in keys and output.system in settings.systems
    ]
    if unknown := sorted({o.system for o in kept if o.fingerprint is None}):
        raise ValueError(
            f'Family `{family.name}`: outputs of {code(unknown)} lack a fingerprint, '
            'so they cannot be published. Rerun them with `uv run bench run`.'
        )

    fingerprints = system_fingerprints(kept)
    published = sorted(
        (_published_output(family.name, keys[o.case], o) for o in kept),
        key=lambda output: output.key,
    )
    if not _pairable(published):
        raise ValueError(
            f'Family `{family.name}` has no {target} case on which two systems '
            'have different passing outputs, so no pair can be voted on.'
        )

    used = {output.case_key for output in published}
    draft = Publication(
        id='',
        family=family.name,
        display_name=settings.display_name,
        aspect=settings.aspect,
        render=settings.render,
        defects=tuple(settings.defects),
        systems={
            system: system_names[system]
            for system in settings.systems
            if system in fingerprints
        },
        cases=tuple(
            sorted(
                (
                    PublishedCase(
                        key=keys[case.id],
                        category=case.category,
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
        raise ValueError(
            ' '.join(
                f'Cases {code(cases)} share an instruction and checklist, so a '
                'voter could not tell them apart.'
                for cases in repeated
            )
        )


def _published_output(family: str, case: str, output: Output) -> PublishedOutput:
    assert output.fingerprint is not None
    verdict = _verdict(output.gate_failures)
    text = output.text or ''
    return PublishedOutput(
        key=output_key(family, output),
        case_key=case,
        system=output.system,
        fingerprint=output.fingerprint,
        verdict=verdict,
        errored=output.error is not None,
        length=text_length(text),
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


# ── Files ──


def write(publication: Publication, outputs: Sequence[Output]) -> Path:
    """Write the publication and its Markdown listing to the data directory.

    `outputs` supplies the gates that failing outputs failed. The listing sits
    beside the returned JSON path with the suffix `.md`.
    """
    path = data_dir() / 'publications' / publication.family / f'{publication.id}.json'
    write_atomically(path, publication.model_dump_json(indent=2) + '\n')
    gates = {
        output_key(publication.family, output): output.gate_failures or []
        for output in outputs
    }
    write_atomically(path.with_suffix('.md'), _listing(publication, gates))
    return path


def read(path: Path) -> Publication:
    """Read a publication, refusing a file edited since it was prepared."""
    publication = Publication.model_validate_json(path.read_bytes())
    derived = publication.identity()
    if derived != publication.id:
        raise ValueError(
            f'Publication `{path}` was edited: its content derives ID `{derived}`, '
            f'but it records `{publication.id}`. Prepare it again with '
            f'`uv run bench arena prepare {publication.family}`.'
        )
    return publication


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


def publish(database: Database, publication: Publication, now: datetime) -> bool:
    """Stage the publication and activate it for its family.

    Each step is idempotent, so rerunning an interrupted publish completes it.
    Returns False when the publication was already active and nothing changed.

    Raises
    ------
    ValueError
        A case's source is neither public nor synthetic, or the staged rows do
        not match the publication.
    """
    if private := [c for c in publication.cases if c.source not in PUBLIC_SOURCES]:
        raise ValueError(
            f'Publication `{publication.id}` holds {len(private)} cases whose source '
            'is neither `public` nor `synthetic`, so it cannot leave this machine.'
        )

    active = database.execute(
        'SELECT publication_id FROM active WHERE family = ?', [publication.family]
    )
    if active.rows and active.rows[0]['publication_id'] == publication.id:
        return False

    milliseconds = int(now.timestamp() * 1000)
    database.execute(
        'INSERT INTO publications (id, family, display_name, aspect, render, '
        'defects, systems, case_count, output_count, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING',
        [
            publication.id,
            publication.family,
            publication.display_name,
            publication.aspect,
            publication.render,
            json.dumps(publication.defects),
            json.dumps(publication.systems, ensure_ascii=False),
            len(publication.cases),
            len(publication.outputs),
            milliseconds,
        ],
    )
    _insert(
        database,
        'cases',
        ('publication_id', 'case_key', 'category', 'instruction', 'checklist'),
        [
            (
                publication.id,
                case.key,
                case.category,
                case.instruction,
                json.dumps(case.checklist, ensure_ascii=False),
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
    activated = database.execute(
        'INSERT INTO active (family, publication_id, activated_at) '
        'SELECT ?, ?, ? '
        'WHERE (SELECT count(*) FROM cases WHERE publication_id = ?) = ? '
        'AND (SELECT count(*) FROM outputs WHERE publication_id = ?) = ? '
        'ON CONFLICT (family) DO UPDATE SET '
        'publication_id = excluded.publication_id, '
        'activated_at = excluded.activated_at',
        [
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
        raise ValueError(
            f'Publication `{publication.id}` is not completely staged, so it was '
            'not activated. Publish the same file again.'
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
            f'INSERT INTO {table} ({", ".join(columns)}) VALUES '
            f'{", ".join([placeholders] * len(chunk))} ON CONFLICT DO NOTHING',
            [value for row in chunk for value in row],
        )


def _digest(value: JsonValue) -> str:
    canonical = json.dumps(
        value, sort_keys=True, separators=(',', ':'), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
