"""Deterministic checks of authored Markdown, separate from generation identity."""

import re
import unicodedata
from collections.abc import Sequence

from markdown_it import MarkdownIt
from markdown_it.rules_inline.state_inline import StateInline
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
    prose_documents = (
        _documents(text or '', _quoted_markdown(context.quoted_input))
        if context.quoted_input
        else documents
    )
    prose = [span for _, tokens in prose_documents for span in _prose(tokens)]
    tree_lines = [
        line
        for _, tokens in documents
        for token in tokens
        if token.type in {'fence', 'code_block'}
        for line in token.content.splitlines()
    ]
    has_content = any(span.strip() for span in prose) or (
        context.kind == 'directory-tree' and any(line.strip() for line in tree_lines)
    )
    valid = completed and has_content
    verdicts: dict[CheckRule, bool] = {}
    for rule in context.rules:
        match rule:
            case 'ellipsis':
                passed = all('…' not in span for span in prose)
            case 'han-punctuation':
                passed = all(_han_punctuation(span) for span in prose)
            case 'curly-quotes':
                passed = all(
                    not any(character in span for character in '‘’“”') for span in prose
                )
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


def _quoted_markdown(quotes: Sequence[str]) -> MarkdownIt:
    markdown = MarkdownIt('commonmark', {'html': False})

    def quoted_input(state: StateInline, silent: bool) -> bool:
        for quote in quotes:
            if state.pos + len(quote) <= state.posMax and state.src.startswith(
                quote, state.pos
            ):
                if not silent:
                    state.push('quoted_input', '', 0)
                state.pos += len(quote)
                return True
        return False

    markdown.inline.ruler.before('text', 'quoted_input', quoted_input)
    for quote in quotes:
        markdown.inline.add_terminator_char(quote[0])
    return markdown


def _documents(
    source: str, markdown: MarkdownIt = _MARKDOWN
) -> list[tuple[str, list[Token]]]:
    tokens = markdown.parse(source)
    documents = [(source, tokens)]
    for token in tokens:
        if token.type == 'fence' and token.info.strip().lower() in _PROSE_FENCES:
            documents.extend(_documents(token.content, markdown))
    return documents


def _prose(tokens: Sequence[Token]) -> list[str]:
    spans: list[str] = []
    for token in tokens:
        if token.type != 'inline':
            continue
        parts: list[str] = []
        for child in token.children or []:
            if child.type == 'text':
                parts.append(child.content)
            elif child.type in {'softbreak', 'hardbreak'}:
                parts.append('\n')
            elif child.type in {'code_inline', 'image', 'quoted_input'}:
                spans.append(''.join(parts))
                parts = []
        spans.append(''.join(parts))
    return spans


def _han_punctuation(span: str) -> bool:
    compact = ''.join(span.split())
    return not any(
        punctuation in ',.:' and _han(before) and _han(after)
        for before, punctuation, after in zip(
            compact, compact[1:], compact[2:], strict=False
        )
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
