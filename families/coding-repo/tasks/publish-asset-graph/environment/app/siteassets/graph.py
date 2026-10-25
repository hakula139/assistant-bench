"""Read and validate the reachable local dependency graph."""

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .models import BuildError
from .references import Reference, scan


@dataclass(frozen=True)
class Node:
    content: bytes
    references: list[tuple[Reference, Path | None]]


def discover(source_root: Path, output_root: Path) -> dict[Path, Node]:
    root = source_root.resolve()
    output = output_root.resolve()

    if root == output or not root.is_dir():
        raise BuildError('Source must be a directory distinct from output.')

    nodes: dict[Path, Node] = {}
    visiting: set[Path] = set()

    def visit(path: Path) -> None:
        path = Path(os.path.abspath(path))
        resolved = path.resolve()

        if not path.is_relative_to(root) or not resolved.is_relative_to(root):
            raise BuildError('A local reference escapes the source tree.')
        if path in visiting:
            raise BuildError(f'Dependency cycle at {path.relative_to(root)}.')
        if path in nodes:
            return
        if not path.is_file():
            raise BuildError(f'Missing dependency {path.relative_to(root)}.')

        visiting.add(path)

        try:
            content = path.read_bytes()
            text = (
                content.decode('utf-8')
                if path.suffix.lower() in {'.html', '.css'}
                else ''
            )
        except (OSError, UnicodeError) as error:
            raise BuildError(f'Cannot read {path.relative_to(root)}.') from error

        references: list[tuple[Reference, Path | None]] = []

        for reference in scan(text, css=path.suffix.lower() == '.css') if text else []:
            parts = urlsplit(reference.url)
            target = None

            if not parts.scheme and not parts.netloc and parts.path:
                decoded = unquote(parts.path)
                target = (
                    root / decoded.lstrip('/')
                    if decoded.startswith('/')
                    else path.parent / decoded
                )
                target = Path(os.path.abspath(target))
                visit(target)

            references.append((reference, target))

        visiting.remove(path)
        nodes[path] = Node(content, references)

    for entry in sorted(root.rglob('*')):
        if entry.suffix.lower() != '.html' or not entry.is_file():
            continue
        if output.is_relative_to(root) and entry.is_relative_to(output):
            continue

        visit(entry)

    return nodes
