"""Rewrite reachable assets and publish their content hashes."""

import hashlib
import html
import posixpath
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit

from .graph import discover
from .models import BuildResult
from .publisher import begin


def build(source_root: Path, output_root: Path) -> BuildResult:
    root = source_root.resolve()
    output = output_root.resolve()
    directory = begin(output)
    nodes = discover(root, output)
    emitted: dict[Path, PurePosixPath] = {}
    manifest: dict[str, str] = {}

    for path, node in nodes.items():
        entry = path.suffix.lower() == '.html'
        parent = (
            PurePosixPath(path.relative_to(root).as_posix()).parent
            if entry
            else PurePosixPath('assets')
        )
        content = node.content

        if node.references:
            text = content.decode('utf-8')

            for reference, target in reversed(node.references):
                if target is None:
                    continue

                relative = posixpath.relpath(
                    emitted[target].as_posix(), parent.as_posix()
                )
                suffix = reference.url[len(urlsplit(reference.url).path) :]
                value = quote(relative, safe='/') + suffix

                if reference.attribute:
                    value = html.escape(value, quote=True)

                text = text[: reference.start] + value + text[reference.end :]

            content = text.encode('utf-8')

        name = (
            PurePosixPath(path.relative_to(root).as_posix())
            if entry
            else PurePosixPath('assets')
            / (
                f'{path.stem}.{hashlib.sha256(node.content).hexdigest()[:12]}{path.suffix}'
            )
        )
        emitted[path] = name
        destination = directory / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)

        if not entry:
            manifest[path.relative_to(root).as_posix()] = name.as_posix()

    return BuildResult(directory, manifest)
