"""Map request targets to files under the served root."""

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote


CONTENT_TYPES = {
    '.css': 'text/css; charset=utf-8',
    '.html': 'text/html; charset=utf-8',
    '.jpg': 'image/jpeg',
    '.js': 'text/javascript; charset=utf-8',
    '.json': 'application/json',
    '.mp3': 'audio/mpeg',
    '.mp4': 'video/mp4',
    '.pdf': 'application/pdf',
    '.png': 'image/png',
    '.svg': 'image/svg+xml',
    '.txt': 'text/plain; charset=utf-8',
    '.zip': 'application/zip',
}
DEFAULT_CONTENT_TYPE = 'application/octet-stream'


@dataclass(frozen=True)
class FileInfo:
    path: Path
    size: int
    modified: int
    """Last modification in whole seconds since the epoch."""

    content_type: str
    etag: str
    """A strong entity tag, quotes included."""


def resolve(root: Path, target: str) -> Path | None:
    """Return the regular file that `target` names under `root`, if any.

    Directories serve their `index.html`. Hidden names, `..` segments and
    symbolic links that leave the root never resolve.
    """
    path = target.partition('?')[0].partition('#')[0]
    if not path.startswith('/'):
        return None

    segments = [segment for segment in unquote(path).split('/') if segment]
    if any(segment.startswith('.') or '\0' in segment for segment in segments):
        return None

    candidate = root.joinpath(*segments)
    if candidate.is_dir():
        candidate /= 'index.html'

    resolved = candidate.resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        return None
    return resolved


def file_info(path: Path) -> FileInfo:
    status = path.stat()
    return FileInfo(
        path=path,
        size=status.st_size,
        modified=int(status.st_mtime),
        content_type=CONTENT_TYPES.get(path.suffix.lower(), DEFAULT_CONTENT_TYPE),
        etag=f'"{status.st_size:x}-{status.st_mtime_ns:x}"',
    )
