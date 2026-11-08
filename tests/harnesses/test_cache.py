import hashlib
import io
import tarfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from assistant_bench.harnesses import cache


class TestArtifact:
    @pytest.mark.parametrize('harness', ['claude-code', 'codex'])
    @pytest.mark.parametrize(
        'valid', [True, False], ids=['verified', 'checksum-mismatch']
    )
    def test_caches_only_an_artifact_matching_the_vendor_checksum(
        self,
        data_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        harness: cache.InstalledHarness,
        valid: bool,
    ) -> None:
        monkeypatch.delenv('GH_TOKEN', raising=False)
        monkeypatch.delenv('GITHUB_TOKEN', raising=False)
        content = b'example CLI binary'
        archive_buffer = io.BytesIO()
        with tarfile.open(fileobj=archive_buffer, mode='w:gz') as archive:
            member = tarfile.TarInfo('codex-aarch64-unknown-linux-musl')
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        downloaded = content if harness == 'claude-code' else archive_buffer.getvalue()
        digest = hashlib.sha256(downloaded).hexdigest() if valid else '0' * 64
        requests: list[str] = []

        def handle(request: httpx.Request) -> httpx.Response:
            assert 'authorization' not in request.headers
            requests.append(request.url.path)
            if request.url.path.endswith('manifest.json'):
                return httpx.Response(
                    200, json={'platforms': {'linux-arm64': {'checksum': digest}}}
                )
            if request.url.path.endswith('rust-v1.2.3'):
                return httpx.Response(
                    200,
                    json={
                        'assets': [
                            {
                                'name': 'codex-aarch64-unknown-linux-musl.tar.gz',
                                'digest': f'sha256:{digest}',
                                'browser_download_url': 'https://example.com/codex.tar.gz',
                            }
                        ]
                    },
                )
            return httpx.Response(200, content=downloaded)

        client = httpx.Client

        def build(**kwargs: Any) -> httpx.Client:
            return client(**kwargs, transport=httpx.MockTransport(handle))

        monkeypatch.setattr(httpx, 'Client', build)

        if valid:
            path = cache.artifact(harness, '1.2.3', 'aarch64')
            first_requests = requests.copy()
            reused = cache.artifact(harness, '1.2.3', 'aarch64')

            assert path.read_bytes() == content
            assert path.stat().st_mode & 0o111
            assert reused == path
            assert requests == first_requests
        else:
            with pytest.raises(ValueError, match='published checksum'):
                cache.artifact(harness, '1.2.3', 'aarch64')

            assert not [
                path
                for path in (data_dir / 'agents').rglob('*')
                if path.is_file() and path.name in ('claude', 'codex')
            ]

    @pytest.mark.parametrize(
        ('token', 'fallback', 'redirected'),
        [
            ('primary-token', 'fallback-token', False),
            (None, 'fallback-token', False),
            ('', 'fallback-token', False),
            ('primary-token', 'fallback-token', True),
        ],
        ids=['gh-token', 'github-token', 'empty-gh-token', 'metadata-redirect'],
    )
    def test_authenticates_only_the_release_metadata_without_following_redirects(
        self,
        monkeypatch: pytest.MonkeyPatch,
        token: str | None,
        fallback: str,
        redirected: bool,
    ) -> None:
        if token is None:
            monkeypatch.delenv('GH_TOKEN', raising=False)
        else:
            monkeypatch.setenv('GH_TOKEN', token)
        monkeypatch.setenv('GITHUB_TOKEN', fallback)
        content = b'example CLI binary'
        archive_buffer = io.BytesIO()
        with tarfile.open(fileobj=archive_buffer, mode='w:gz') as archive:
            member = tarfile.TarInfo('codex-aarch64-unknown-linux-musl')
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        downloaded = archive_buffer.getvalue()
        digest = hashlib.sha256(downloaded).hexdigest()
        requests: list[str] = []

        def handle(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            if request.url.path.endswith('rust-v1.2.3'):
                assert request.headers['authorization'] == f'Bearer {token or fallback}'
                if redirected:
                    return httpx.Response(
                        302, headers={'location': 'https://api.github.com/redirected'}
                    )
                return httpx.Response(
                    200,
                    json={
                        'assets': [
                            {
                                'name': 'codex-aarch64-unknown-linux-musl.tar.gz',
                                'digest': f'sha256:{digest}',
                                'browser_download_url': 'https://example.com/codex.tar.gz',
                            }
                        ]
                    },
                )
            assert 'authorization' not in request.headers
            if request.url.host == 'example.com':
                return httpx.Response(
                    302, headers={'location': 'https://assets.example.com/codex.tar.gz'}
                )
            assert request.url.host == 'assets.example.com'
            return httpx.Response(200, content=downloaded)

        client = httpx.Client

        def build(**kwargs: Any) -> httpx.Client:
            return client(**kwargs, transport=httpx.MockTransport(handle))

        monkeypatch.setattr(httpx, 'Client', build)

        if redirected:
            with pytest.raises(httpx.HTTPStatusError):
                cache.artifact('codex', '1.2.3', 'aarch64')

            assert requests == [f'{cache.CODEX_RELEASES}/rust-v1.2.3']
        else:
            path = cache.artifact('codex', '1.2.3', 'aarch64')

            assert path.read_bytes() == content
            assert requests == [
                f'{cache.CODEX_RELEASES}/rust-v1.2.3',
                'https://example.com/codex.tar.gz',
                'https://assets.example.com/codex.tar.gz',
            ]
