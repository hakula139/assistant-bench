"""Verified, versioned Linux CLI artifacts uploaded into task containers."""

import hashlib
import io
import re
import tarfile
from pathlib import Path
from typing import Literal

import httpx
from filelock import FileLock

from assistant_bench.messages import InputError
from assistant_bench.paths import data_dir


InstalledHarness = Literal['claude-code', 'codex']
CLAUDE_RELEASES = 'https://downloads.claude.ai/claude-code-releases'
CODEX_RELEASES = 'https://api.github.com/repos/openai/codex/releases/tags'
PLATFORMS = {'aarch64': 'arm64', 'x86_64': 'x64'}


def artifact(harness: InstalledHarness, version: str, architecture: str) -> Path:
    """Cache a pinned vendor artifact, verifying its published SHA-256 first."""
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise InputError(
            'An installed agent needs a pinned `major.minor.patch` version.'
        )
    if architecture not in PLATFORMS:
        raise InputError(
            f'Installed agents do not support architecture `{architecture}`.'
        )
    platform = f'linux-{PLATFORMS[architecture]}'
    command = 'claude' if harness == 'claude-code' else 'codex'
    path = data_dir() / 'agents' / harness / version / platform / command
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path.with_suffix('.lock'))):
        if not path.is_file():
            _download(harness, version, architecture, platform, path)
    return path


def _download(
    harness: InstalledHarness,
    version: str,
    architecture: str,
    platform: str,
    path: Path,
) -> None:
    with httpx.Client(follow_redirects=True, timeout=120) as client:
        if harness == 'claude-code':
            manifest = client.get(f'{CLAUDE_RELEASES}/{version}/manifest.json')
            manifest.raise_for_status()
            checksum = manifest.json()['platforms'][platform]['checksum']
            response = client.get(f'{CLAUDE_RELEASES}/{version}/{platform}/claude')
            response.raise_for_status()
            content = response.content
            _verify(content, checksum)
        else:
            target = f'codex-{architecture}-unknown-linux-musl'
            name = f'{target}.tar.gz'
            release = client.get(f'{CODEX_RELEASES}/rust-v{version}')
            release.raise_for_status()
            asset = next(
                asset for asset in release.json()['assets'] if asset['name'] == name
            )
            response = client.get(asset['browser_download_url'])
            response.raise_for_status()
            _verify(response.content, asset['digest'].removeprefix('sha256:'))
            with tarfile.open(
                fileobj=io.BytesIO(response.content), mode='r:gz'
            ) as archive:
                member = archive.extractfile(target)
                if member is None:
                    raise InputError(
                        'The Codex release archive contains no executable.'
                    )
                with member:
                    content = member.read()

    temporary = path.with_suffix('.download')
    temporary.write_bytes(content)
    temporary.chmod(0o755)
    temporary.replace(path)


def _verify(content: bytes, expected: str) -> None:
    if hashlib.sha256(content).hexdigest() != expected:
        raise InputError(
            'The installed agent download differs from its published checksum.'
        )
