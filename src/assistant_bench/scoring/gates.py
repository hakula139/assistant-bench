"""Eligibility gates, which decide whether a voted output reaches the owner."""

import difflib
import re
from enum import StrEnum

from opencc import OpenCC

from assistant_bench.families import Language, VotesSettings


class Gate(StrEnum):
    """A check whose failure resolves every pair holding the output automatically."""

    EMPTY = 'empty'
    """The output is blank."""
    TRUNCATED = 'truncated'
    """The endpoint reported a finish reason other than `stop`, or none."""
    LENGTH = 'length'
    """The stripped output falls outside the family's `min_chars` and `max_chars`."""
    LANGUAGE = 'language'
    """The output's prose and code comments are mostly in another language."""
    SCRIPT = 'script'
    """The output holds Traditional characters that the instruction lacks."""


_TO_SIMPLIFIED = OpenCC('t2s')

# A fenced code block, capturing its fence and its body.
_FENCED = re.compile(r'^(`{3,}|~{3,})[^\n]*\n(.*?)^\1[ \t]*$', re.MULTILINE | re.DOTALL)
_INLINE_CODE = re.compile(r'(`+).+?\1')
_URL = re.compile(r'https?://\S+')
# A line comment opened by `#` or `//`, leaving out shebangs and `#[attribute]`.
_LINE_COMMENT = re.compile(r'(?:^|\s)(?:#(?![!\[{])|//)(.*)$', re.MULTILINE)
_BLOCK_COMMENT = re.compile(r'/\*(.*?)\*/|"""(.*?)"""', re.DOTALL)
_HAN = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0003134f]')
_LATIN_WORD = re.compile(r"[A-Za-z]+(?:['-][A-Za-z]+)*")


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
        The case instruction, whose own Traditional characters an output may repeat.
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

    Natural language is the prose outside code, without inline code or URLs, plus
    the comments and docstrings inside fenced code blocks, so code-only outputs are
    judged by their comments. Han characters count against Latin words, and text
    with neither returns `None`.
    """
    parts = [_URL.sub('', _INLINE_CODE.sub('', _FENCED.sub('', text)))]
    for block in _FENCED.finditer(text):
        body = block.group(2)
        parts.extend(match.group(1) for match in _LINE_COMMENT.finditer(body))
        parts.extend(
            ''.join(match.groups('')) for match in _BLOCK_COMMENT.finditer(body)
        )
    natural = '\n'.join(parts)

    han = len(_HAN.findall(natural))
    words = len(_LATIN_WORD.findall(natural))
    if han == words == 0:
        return None
    return 'zh' if han > words else 'en'


def traditional_characters(text: str, source: str) -> list[str]:
    """Return the characters that a Simplified conversion changes, absent from `source`.

    The conversion runs over the whole text, so OpenCC's phrase tables keep
    characters that are Simplified in context, such as the 乾 of 乾坤.
    """
    converted: str = _TO_SIMPLIFIED.convert(text)
    changed = (
        text[start:end]
        for tag, start, end, _, _ in difflib.SequenceMatcher(
            None, text, converted, autojunk=False
        ).get_opcodes()
        if tag != 'equal'
    )
    found = dict.fromkeys(char for span in changed for char in span)
    return [char for char in found if char not in source and _HAN.match(char)]
