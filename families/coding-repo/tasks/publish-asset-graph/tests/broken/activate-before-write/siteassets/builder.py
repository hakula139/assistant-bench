"""Publish a validated release before writing its files."""

import hashlib
import html
import posixpath
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit

from siteassets.graph import discover
from siteassets.models import BuildResult
from siteassets.publisher import activate, begin, discard


def build(source_root: Path, output_root: Path) -> BuildResult:
    root = source_root.resolve()
    output = output_root.resolve()
    nodes = discover(root, output)
    emitted: dict[Path, PurePosixPath] = {}
    manifest: dict[str, str] = {}
    files: dict[PurePosixPath, bytes] = {}

    for path, node in nodes.items():
        entry = path.suffix.lower() == '.html'
        relative_path = PurePosixPath(path.relative_to(root).as_posix())
        parent = relative_path.parent if entry else PurePosixPath('assets')
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
            relative_path
            if entry
            else PurePosixPath('assets')
            / f'{path.stem}.{hashlib.sha256(content).hexdigest()[:12]}{path.suffix}'
        )
        emitted[path] = name
        files[name] = content

        if not entry:
            manifest[relative_path.as_posix()] = name.as_posix()

    directory = begin(output)

    try:
        activate(output, directory)

        for name, content in files.items():
            destination = directory / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
    except BaseException:
        discard(directory)
        raise

    return BuildResult(directory, manifest)
