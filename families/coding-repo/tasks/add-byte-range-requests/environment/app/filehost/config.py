"""Server settings."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ServerConfig:
    root: Path
    host: str = '127.0.0.1'
    port: int = 8000
    min_compress_bytes: int = 256
    """Smaller compressible files are always sent as they are."""

    idle_timeout: float = 5.0
    """Seconds a kept-alive connection may wait for its next request."""
