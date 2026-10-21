"""Eligibility gates, which decide whether a voted output reaches the owner."""

import re

from zhon import hanzi
from zhon.cedict.simplified import CHARACTERS as SIMPLIFIED
from zhon.cedict.traditional import CHARACTERS as TRADITIONAL

from assistant_bench.families import Gate, Language, VotesSettings


# CC-CEDICT's Traditional characters minus its Simplified ones, so a character valid
# in both scripts, such as 乾 or 於, never fails the script gate.
_TRADITIONAL_ONLY = frozenset(TRADITIONAL) - frozenset(SIMPLIFIED)

# Fence info strings whose block holds prose, such as a commit message.
_PROSE_FENCES = frozenset({'markdown', 'md', 'text', 'txt'})
# A fenced code block, capturing its fence, its info string, and its body.
_FENCED = re.compile(
    r'^(`{3,}|~{3,})[ \t]*(\S*)[^\n]*\n(.*?)^\1[ \t]*$', re.MULTILINE | re.DOTALL
)
_INLINE_CODE = re.compile(r'(`+).+?\1')
# A URL, which ends at whitespace, Han characters, or Chinese punctuation.
_URL = re.compile(f'https?://[^\\s{hanzi.characters}{re.escape(hanzi.punctuation)}]+')
# A line comment opened by `#` and a space or by `//`, leaving out `#include`, CSS
# colors, shebangs, and `#[attribute]`.
_LINE_COMMENT = re.compile(r'(?:^|\s)(?:#(?=\s)|//)(.*)$', re.MULTILINE)
_BLOCK_COMMENT = re.compile(r'/\*(.*?)\*/|"""(.*?)"""|\'\'\'(.*?)\'\'\'', re.DOTALL)
_HAN = re.compile(f'[{hanzi.characters}]')
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

    Natural language is the prose outside code, without inline code or URLs, and
    the body of each fence tagged `markdown`, `md`, `text`, or `txt`. Every other
    fenced block contributes only its comments: lines opened by `# ` or `//`,
    `/* */` blocks, and triple-quoted docstrings, so code-only outputs are judged by
    their comments. Han characters count against Latin words, and text with neither
    returns `None`.
    """
    parts = [_URL.sub('', _INLINE_CODE.sub('', _FENCED.sub('', text)))]
    for block in _FENCED.finditer(text):
        info, body = block.group(2).lower(), block.group(3)
        if info in _PROSE_FENCES:
            parts.append(_URL.sub('', body))
            continue
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
    """Return the Traditional-only characters of `text` that `source` lacks.

    A character counts when CC-CEDICT lists it as Traditional and never as
    Simplified, so the check misses a dual-use character in a Traditional word,
    such as the 於 of 於是, and never flags correct Simplified text.
    """
    allowed = set(source)
    return list(
        dict.fromkeys(
            char for char in text if char in _TRADITIONAL_ONLY and char not in allowed
        )
    )
