"""Portable, hash-checked captures of every section of a sealed ledger export."""

import json
import os
from pathlib import Path
from typing import Protocol, get_args

from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter, model_validator

from assistant_bench_arena.ledger_models import Export, ExportSection, Page, Row
from assistant_bench_arena.serialization import canonical, digest


_ROW = TypeAdapter(Row)
_SECTIONS = get_args(ExportSection.__value__)


class ExportReader(Protocol):
    async def page(
        self,
        export_id: str,
        section: ExportSection,
        cursor: str | None = None,
        *,
        limit: int = 250,
    ) -> Page: ...


class Section(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)

    name: ExportSection
    rows: tuple[str, ...]
    """Canonical JSON objects, held as immutable strings in the saved capture."""

    @model_validator(mode='after')
    def validate_rows(self) -> Section:
        if len(self.rows) != len(set(self.rows)):
            raise ValueError('An export section contains duplicate rows.')

        for row in self.rows:
            if canonical(_ROW.validate_json(row)) != row:
                raise ValueError('Export rows must use canonical JSON.')

        return self


class FrozenExport(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)

    header: Export
    sections: tuple[Section, ...]
    content_digest: str

    @model_validator(mode='after')
    def validate_capture(self) -> FrozenExport:
        if tuple(section.name for section in self.sections) != _SECTIONS:
            raise ValueError(
                'An export must contain every section exactly once in order.'
            )

        if any(
            len(section.rows) != self.header.sections[section.name]
            for section in self.sections
        ):
            raise ValueError('Export section counts do not match the registered cut.')

        payload = self.model_dump(mode='json', exclude={'content_digest'})
        if digest(payload) != self.content_digest:
            raise ValueError('The export content digest does not match its capture.')

        return self

    def rows(self, name: ExportSection) -> tuple[Row, ...]:
        section = next(section for section in self.sections if section.name == name)
        return tuple(_ROW.validate_json(row) for row in section.rows)


async def capture_export(ledger: ExportReader, header: Export) -> FrozenExport:
    """Read bounded native pages from one immutable export registration."""
    sections: list[Section] = []

    for name in _SECTIONS:
        rows: list[str] = []
        cursor: str | None = None
        seen: set[str] = set()

        while True:
            page = await ledger.page(header.id, name, cursor)
            rows.extend(canonical(row) for row in page.rows)
            cursor = page.cursor
            if cursor is None:
                break

            if cursor in seen:
                raise ValueError('The export repeated a pagination cursor.')

            seen.add(cursor)

        sections.append(Section(name=name, rows=tuple(rows)))

    payload: dict[str, JsonValue] = {
        'header': header.model_dump(mode='json'),
        'sections': [section.model_dump(mode='json') for section in sections],
    }
    return FrozenExport.model_validate_json(
        canonical({**payload, 'content_digest': digest(payload)})
    )


def read_export(path: Path) -> FrozenExport:
    return FrozenExport.model_validate_json(path.read_bytes())


def write_export(capture: FrozenExport, path: Path) -> None:
    """Create a private capture without replacing an existing review artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(
        path,
        'x',
        encoding='utf-8',
        opener=lambda name, flags: os.open(name, flags, 0o600),
    ) as file:
        json.dump(
            capture.model_dump(mode='json'), file, ensure_ascii=False, allow_nan=False
        )
        file.write('\n')
