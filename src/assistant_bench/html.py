"""Extract unchanged HTML documents that satisfy the voting isolation contract."""

import re

from justhtml import JustHTML, stream
from markdown_it import MarkdownIt


_MARKDOWN = MarkdownIt('commonmark')
_WHITESPACE = ' \t\n\r\f'
_BOUNDARIES = ('doctype', 'html', 'head', '/head', 'body', '/body', '/html')
_RESOURCE_HINTS = frozenset(
    {'dns-prefetch', 'preconnect', 'prefetch', 'preload', 'prerender', 'modulepreload'}
)


def extract_html(text: str) -> str | None:
    """Return the exact bare or singly fenced document, or `None` if ineligible.

    Documents require explicit ordered HTML boundaries and may navigate only
    within themselves. The caller must serve them with the preview sandbox CSP.
    """
    source = _document(text)
    if source is None or not _boundaries(source):
        return None

    document = JustHTML(source, sanitize=False, scripting_enabled=False)
    for node in document.query('*'):
        attrs = {key: value or '' for key, value in (node.attrs or {}).items()}
        if node.name == 'base':
            return None
        if node.name == 'link' and _RESOURCE_HINTS.intersection(
            attrs.get('rel', '').lower().split()
        ):
            return None
        if node.name == 'meta':
            directive = attrs.get('http-equiv', '').strip(_WHITESPACE).lower()
            if directive == 'refresh' or (
                directive == 'x-dns-prefetch-control'
                and attrs.get('content', '').strip(_WHITESPACE).lower() == 'on'
            ):
                return None
        if node.name in {'a', 'area'} and any(
            not value.strip(_WHITESPACE).startswith('#')
            for key in ('href', 'xlink:href')
            if (value := attrs.get(key)) is not None
        ):
            return None
        if 'target' in attrs and attrs['target'].lower() != '_self':
            return None
    return source


def _document(text: str) -> str | None:
    tokens = _MARKDOWN.parse(text)
    if not any(token.type == 'fence' for token in tokens):
        return text
    if len(tokens) != 1:
        return None
    token = tokens[0]
    if (
        token.type != 'fence'
        or token.info.strip().lower() != 'html'
        or token.map is None
    ):
        return None

    lines = re.findall(r'[^\r\n]*(?:\r\n|\r|\n|$)', text)
    start, end = token.map
    closing = lines[end - 1].rstrip('\r\n')
    marker = re.escape(token.markup[0])
    if not re.fullmatch(rf' {{0,3}}{marker}{{{len(token.markup)},}}[ \t]*', closing):
        return None
    return ''.join(lines[start + 1 : end - 1])


def _boundaries(source: str) -> bool:
    position = 0
    for event, data in stream(source):
        boundary = None
        if event == 'doctype':
            if data[0] != 'html':
                return False
            boundary = 'doctype'
        elif event == 'start' and data[0] in {'html', 'head', 'body'}:
            boundary = data[0]
        elif event == 'end' and data in {'html', 'head', 'body'}:
            boundary = f'/{data}'
        if boundary is not None:
            if position == len(_BOUNDARIES) or boundary != _BOUNDARIES[position]:
                return False
            position += 1
        elif position not in {3, 5} and event != 'comment':
            if event != 'text' or not isinstance(data, str) or data.strip(_WHITESPACE):
                return False
    return position == len(_BOUNDARIES)
