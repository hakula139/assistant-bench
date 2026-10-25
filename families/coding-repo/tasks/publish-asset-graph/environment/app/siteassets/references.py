"""Locate supported URL values without changing their surrounding syntax."""

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser


_ATTRIBUTE = re.compile(
    r"""(?P<name>[^\s=<>/]+)\s*=\s*(?:"(?P<double>[^"]*)"|'(?P<single>[^']*)'|(?P<bare>[^\s"'=<>]+))"""
)
_CSS = re.compile(
    r"""/\*.*?\*/|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|(?<![-\w\u0080-\U0010ffff])(?i:url)\(\s*(?:"(?P<double>[^"]*)"|'(?P<single>[^']*)'|(?P<bare>[^)\s]+))\s*\)""",
    re.DOTALL,
)


@dataclass(frozen=True)
class Reference:
    start: int
    end: int
    url: str
    attribute: bool


class _Document(HTMLParser):
    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=True)
        self.offsets = [0]
        self.references: list[Reference] = []

        for line in text.splitlines(keepends=True):
            self.offsets.append(self.offsets[-1] + len(line))

        self.feed(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        raw = self.get_starttag_text()
        assert raw is not None
        line, column = self.getpos()
        offset = self.offsets[line - 1] + column
        stylesheet = (
            tag == 'link'
            and 'stylesheet' in (dict(attrs).get('rel') or '').lower().split()
        )

        for match in _ATTRIBUTE.finditer(raw):
            name = match['name'].lower()

            if name != 'src' and not (name == 'href' and stylesheet):
                continue

            group = next(
                item for item in ('double', 'single', 'bare') if match[item] is not None
            )
            start, end = match.span(group)
            self.references.append(
                Reference(
                    offset + start, offset + end, html.unescape(match[group]), True
                )
            )

    handle_startendtag = handle_starttag


def scan(text: str, *, css: bool) -> list[Reference]:
    if not css:
        return _Document(text).references

    references: list[Reference] = []

    for match in _CSS.finditer(text):
        group = next(
            (item for item in ('double', 'single', 'bare') if match[item] is not None),
            None,
        )

        if group is not None:
            start, end = match.span(group)
            references.append(Reference(start, end, match[group], False))

    return references
