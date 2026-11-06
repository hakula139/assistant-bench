import shutil
from pathlib import Path


def segments(directory: Path) -> list[Path]:
    """Return the store's segments in sequence order."""
    return sorted(directory.glob('wal-*.log'), key=lambda path: int(path.stem[4:]))


def write(directory: str, files: dict[str, str]) -> None:
    """Write each named file from its hex content into `directory`."""
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (path / name).write_bytes(bytes.fromhex(content))


def segment_count(directory: str) -> int:
    return len(segments(Path(directory)))


def damage_sealed(directory: str, kind: str) -> None:
    """Flip a byte near the end of the first segment, or cut its last three."""
    sealed = segments(Path(directory))[0]
    data = bytearray(sealed.read_bytes())
    if kind == 'flipped byte':
        data[-2] ^= 0xFF
    else:
        del data[-3:]
    sealed.write_bytes(bytes(data))


def damage_snapshot(directory: str) -> None:
    """Flip the middle byte of the snapshot."""
    snapshot = Path(directory) / 'snapshot.db'
    data = bytearray(snapshot.read_bytes())
    data[len(data) // 2] ^= 0xFF
    snapshot.write_bytes(bytes(data))


def append(directory: str, data: str) -> None:
    """Append the hex `data` to the active segment."""
    with segments(Path(directory))[-1].open('ab') as file:
        file.write(bytes.fromhex(data))


def copy(source: str, target: str) -> None:
    shutil.copytree(source, target, symlinks=True)
