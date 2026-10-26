"""Eligibility gates, which decide whether a voted output reaches the owner."""

import re
from collections.abc import Iterator
from html.parser import HTMLParser
from itertools import count

from markdown_it import MarkdownIt
from zhon import hanzi
from zhon.cedict.simplified import CHARACTERS as SIMPLIFIED
from zhon.cedict.traditional import CHARACTERS as TRADITIONAL

from assistant_bench.families import Gate, Language, VotesSettings


_TRADITIONAL_ONLY = frozenset(TRADITIONAL) - frozenset(SIMPLIFIED)

_MARKDOWN = MarkdownIt('commonmark')
# Fence info strings whose block holds Markdown or prose, such as a commit message.
_PROSE_FENCES = frozenset({'markdown', 'md', 'text', 'txt'})
# A URL, which ends at whitespace, Han characters, or Chinese punctuation.
_URL = re.compile(f'https?://[^\\s{hanzi.characters}{re.escape(hanzi.punctuation)}]+')
# A comment to the line's end, opened by `#` and whitespace or by `//` at line
# start or after whitespace, so `#include`, CSS colors, shebangs, and `#[attribute]`
# never match.
_LINE_COMMENT = re.compile(r'(?:^|\s)(?:#(?=\s)|//)(.*)$', re.MULTILINE)
_BLOCK_COMMENT = re.compile(r'/\*(.*?)\*/|"""(.*?)"""|\'\'\'(.*?)\'\'\'', re.DOTALL)
_HAN = re.compile(f'[{hanzi.characters}]')
_LATIN_WORD = re.compile(r"[A-Za-z]+(?:['-][A-Za-z]+)*")
# CommonMark's first HTML block starts with one of these tags, and ends at any of
# their closing tags or, without one, at the end of the document.
_RAW_BLOCK = re.compile(
    r' {0,3}<(?:pre|script|style|textarea)(?=[\s>]|$)', re.IGNORECASE
)
_RAW_BLOCK_END = re.compile(r'</(?:pre|script|style|textarea)>', re.IGNORECASE)


class _Prose:
    def __init__(self) -> None:
        self._open: list[tuple[int, str]] = []
        self._ids = count()
        self._closed: set[int] = set()
        self._data: list[tuple[str, int | None]] = []

    def feed(self, markup: str) -> None:
        parser = _Fragment(self)
        parser.feed(markup)
        parser.close()

    def start(self, tag: str) -> None:
        if tag in {'script', 'style', 'code', 'pre'}:
            self._open.append((next(self._ids), tag))

    def end(self, tag: str) -> None:
        if not any(name == tag for _, name in self._open):
            return
        while self._open:
            index, name = self._open.pop()
            self._closed.add(index)
            if name == tag:
                return

    def data(self, text: str) -> None:
        self._data.append((text, self._open[-1][0] if self._open else None))

    def visible(self) -> list[str]:
        return [text for text, index in self._data if index not in self._closed]


class _Fragment(HTMLParser):
    def __init__(self, prose: _Prose) -> None:
        super().__init__()
        self._prose = prose

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._prose.start(tag)

    def handle_endtag(self, tag: str) -> None:
        self._prose.end(tag)

    def handle_data(self, data: str) -> None:
        self._prose.data(data)


def failed_gates(
    settings: VotesSettings, text: str, finish_reason: str | None, instruction: str
) -> list[Gate]:
    """Return the gates an output fails, empty when the output may reach the owner.

    Parameters
    ----------
    settings
        The family's settings, whose `language` and `gates` select the checks
        beyond the empty and truncation checks every voted family applies.
    text
        The output as the agent wrote it.
    finish_reason
        The endpoint's finish reason, or `None` when it reported none.
    instruction
        The case instruction, whose own Traditional characters an output may
        repeat. Only the script gate reads it.
    """
    failures = [] if finish_reason == 'stop' else [Gate.TRUNCATED]
    stripped = text.strip()
    if not stripped:
        return [Gate.EMPTY, *failures]

    gates = settings.gates
    if (gates.min_chars is not None and len(stripped) < gates.min_chars) or (
        gates.max_chars is not None and len(stripped) > gates.max_chars
    ):
        failures.append(Gate.LENGTH)
    if settings.language is not None and detect_language(text) not in (
        None,
        settings.language,
    ):
        failures.append(Gate.LANGUAGE)
    if gates.script == 'hans' and traditional_characters(text, instruction):
        failures.append(Gate.SCRIPT)
    return failures


def detect_language(text: str) -> Language | None:
    """Return `zh` or `en`, whichever dominates the text's natural language.

    The text is parsed as CommonMark. Natural language includes inline text and
    visible HTML prose, excluding inline code, closed HTML code/pre and
    script/style elements, attributes, markup comments, and URLs. An element left
    open contributes its content, so a stray raw tag hides no later prose. Each
    fence tagged `markdown`, `md`, `text`, or `txt` is parsed the same way. Other
    code blocks contribute only their comments: those opened by `#` and whitespace
    or by `//` at a line's start or after whitespace, `/* */` blocks, and
    triple-quoted strings, so code-only outputs are judged by their comments. Han
    characters count against Latin words, and a tie, including text with neither,
    returns `None`.
    """
    natural = _URL.sub('', '\n'.join(_natural_language(text)))

    han = len(_HAN.findall(natural))
    words = len(_LATIN_WORD.findall(natural))
    if han == words:
        return None
    return 'zh' if han > words else 'en'


def _natural_language(markdown: str) -> Iterator[str]:
    prose = _Prose()
    _collect(prose, markdown)
    yield from prose.visible()


def _collect(prose: _Prose, markdown: str) -> None:
    for token in _MARKDOWN.parse(markdown):
        if token.type == 'inline':
            for child in token.children or []:
                if child.type == 'html_inline':
                    prose.feed(child.content)
                elif child.type == 'text':
                    prose.data(child.content)
        elif token.type == 'html_block':
            if _RAW_BLOCK.match(token.content) and not _RAW_BLOCK_END.search(
                token.content
            ):
                line, _, rest = token.content.partition('\n')
                prose.feed(line)
                _collect(prose, rest)
            else:
                prose.feed(token.content)
        elif (
            token.type == 'fence'
            and (token.info.split() or [''])[0].lower() in _PROSE_FENCES
        ):
            for text in _natural_language(token.content):
                prose.data(text)
        elif token.type in ('fence', 'code_block'):
            for match in _LINE_COMMENT.finditer(token.content):
                prose.data(match.group(1))
            for match in _BLOCK_COMMENT.finditer(token.content):
                prose.data(''.join(match.groups('')))


def traditional_characters(text: str, source: str) -> list[str]:
    """Return the Traditional-only characters of `text` that `source` lacks.

    A character counts when CC-CEDICT lists it as Traditional and never as
    Simplified, so the check misses a dual-use character in a Traditional word,
    such as the 於 of 於是, and never flags a character it also lists as Simplified.
    """
    allowed = set(source)
    return list(
        dict.fromkeys(
            char for char in text if char in _TRADITIONAL_ONLY and char not in allowed
        )
    )
