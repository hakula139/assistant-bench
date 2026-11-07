"""Deterministic checks of authored Markdown, separate from generation identity."""

import re
import unicodedata
from collections.abc import Sequence

from markdown_it import MarkdownIt
from markdown_it.token import Token
from pydantic import BaseModel, ConfigDict

from assistant_bench.families import CheckContext, CheckRule


REVISION = 'format-1'
_MARKDOWN = MarkdownIt('commonmark', {'html': False})
_PROSE_FENCES = {'md', 'markdown', 'text', 'txt'}
_TREE_COMMENT = re.compile(r'\S([ \t]+)(#\s.*)$')


class Checks(BaseModel):
    """A collection-time verdict for each applicable rule at one grader revision."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    revision: str
    rules: dict[CheckRule, bool]

    @property
    def passed(self) -> bool:
        return bool(self.rules) and all(self.rules.values())


def evaluate(context: CheckContext, text: str | None, *, completed: bool) -> Checks:
    """Check one response. An incomplete or prose-free reply fails every rule."""
    source = text or ''
    for quote in context.quoted_input:
        source = source.replace(quote, re.sub(r'[^\n]', ' ', quote))
    documents = _documents(source)
    prose = '\n'.join(_prose(tokens) for _, tokens in documents)
    tree_lines = [
        line
        for _, tokens in documents
        for token in tokens
        if token.type in {'fence', 'code_block'}
        for line in token.content.splitlines()
    ]
    has_content = bool(prose.strip()) or (
        context.kind == 'directory-tree' and any(line.strip() for line in tree_lines)
    )
    valid = completed and has_content
    verdicts: dict[CheckRule, bool] = {}
    for rule in context.rules:
        match rule:
            case 'ellipsis':
                passed = '…' not in prose
            case 'han-punctuation':
                passed = not any(
                    punctuation in ',.:' and _han(before) and _han(after)
                    for before, punctuation, after in zip(
                        prose, prose[1:], prose[2:], strict=False
                    )
                )
            case 'curly-quotes':
                passed = not any(character in prose for character in '‘’“”')
            case 'paragraph-lines':
                passed = all(
                    token.map is None or token.map[1] - token.map[0] == 1
                    for _, tokens in documents
                    for token in tokens
                    if token.type == 'paragraph_open'
                )
            case 'list-spacing':
                passed = all(_list_spacing(raw, tokens) for raw, tokens in documents)
            case 'tree-comments':
                comments = [
                    match
                    for line in tree_lines
                    if (match := _TREE_COMMENT.search(line)) is not None
                ]
                passed = (
                    len(comments) >= 2
                    and len({match.start(2) for match in comments}) == 1
                    and all(
                        len(match[1]) >= 3 and '\t' not in match[1]
                        for match in comments
                    )
                )
            case 'forbidden-marker':
                assert context.forbidden_marker is not None
                passed = context.forbidden_marker not in (text or '')
        verdicts[rule] = valid and passed
    return Checks(revision=REVISION, rules=verdicts)


def _documents(source: str) -> list[tuple[str, list[Token]]]:
    tokens = _MARKDOWN.parse(source)
    documents = [(source, tokens)]
    for token in tokens:
        if token.type == 'fence' and token.info.strip().lower() in _PROSE_FENCES:
            documents.extend(_documents(token.content))
    return documents


def _prose(tokens: Sequence[Token]) -> str:
    return '\n'.join(
        ''.join(
            child.content if child.type == 'text' else '\n'
            for child in token.children or []
            if child.type in {'text', 'softbreak', 'hardbreak'}
        )
        for token in tokens
        if token.type == 'inline'
    )


def _list_spacing(source: str, tokens: Sequence[Token]) -> bool:
    lines = source.splitlines()
    for index, opening in enumerate(tokens):
        if opening.type not in {'bullet_list_open', 'ordered_list_open'}:
            continue
        end = next(
            stop
            for stop in range(index + 1, len(tokens))
            if tokens[stop].level == opening.level and tokens[stop].nesting == -1
        )
        items = [
            item
            for item in tokens[index + 1 : end]
            if item.type == 'list_item_open' and item.level == opening.level + 1
        ]
        blocks = [
            token
            for token in tokens[index + 1 : end]
            if token.level == opening.level + 2 and token.map is not None
        ]
        complex_items = len(blocks) > len(items) or any(
            token.type != 'paragraph_open' for token in blocks
        )
        for item in items[1:]:
            assert item.map is not None
            if _blank(lines[item.map[0] - 1]) != complex_items:
                return False
        if complex_items:
            starts = {item.map[0] for item in items if item.map is not None}
            for token in blocks:
                assert token.map is not None
                start = token.map[0]
                if start not in starts and not _blank(lines[start - 1]):
                    return False
    return True


def _blank(line: str) -> bool:
    return re.fullmatch(r'\s*(?:>\s*)*', line) is not None


def _han(character: str) -> bool:
    return unicodedata.name(character, '').startswith(
        ('CJK UNIFIED IDEOGRAPH-', 'CJK COMPATIBILITY IDEOGRAPH-')
    )
