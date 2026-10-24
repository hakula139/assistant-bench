from urllib.parse import urlunsplit
from uuid import uuid4

import pytest
from pydantic import SecretBytes, ValidationError

from assistant_bench_arena.runtime_models import (
    AdmissionClaims,
    KeyRing,
    KeyVersion,
    Profile,
)


class TestAdmissionClaims:
    @pytest.mark.parametrize(
        ('operation', 'credential'), [('new', 'credential'), ('renew', None)]
    )
    def test_requires_credential_identity_only_for_renewal(
        self, operation: str, credential: str | None
    ) -> None:
        with pytest.raises(ValidationError):
            AdmissionClaims.model_validate(
                {
                    'namespace': 'test',
                    'issued_at': 1000,
                    'expires_at': 2000,
                    'flow': uuid4(),
                    'operation': operation,
                    'original_credential': credential,
                }
            )

    @pytest.mark.parametrize(
        ('issued_at', 'expires_at'), [(1000, 1000), (2000, 1000), (True, 2000)]
    )
    def test_rejects_empty_reversed_or_boolean_lifetimes(
        self, issued_at: int, expires_at: int
    ) -> None:
        with pytest.raises(ValidationError):
            AdmissionClaims(
                namespace='test',
                issued_at=issued_at,
                expires_at=expires_at,
                flow=uuid4(),
                operation='new',
            )


class TestKeyRing:
    @pytest.mark.parametrize('length', [0, 16, 31])
    def test_rejects_a_short_key(self, length: int) -> None:
        with pytest.raises(ValidationError):
            KeyVersion(version='first', starts_at=0, key=SecretBytes(b'a' * length))

    @pytest.mark.parametrize(
        'versions',
        [
            (),
            (('first', 0), ('first', 1000)),
            (('first', 0), ('second', 0)),
            (('first', 1000), ('second', 0)),
        ],
        ids=['empty', 'duplicate-version', 'duplicate-epoch', 'reversed-epochs'],
    )
    def test_rejects_ambiguous_rotation_order(
        self, versions: tuple[tuple[str, int], ...]
    ) -> None:
        keys = tuple(
            KeyVersion(version=name, starts_at=epoch, key=SecretBytes(b'a' * 32))
            for name, epoch in versions
        )

        with pytest.raises(ValidationError):
            KeyRing(versions=keys)

    def test_selects_only_a_retained_key_covering_the_window(self) -> None:
        first = KeyVersion(version='first', starts_at=1000, key=SecretBytes(b'a' * 32))
        second = KeyVersion(
            version='second', starts_at=2000, key=SecretBytes(b'b' * 32)
        )
        keys = KeyRing(versions=(first, second))

        assert keys.at(1000) == first
        assert keys.at(1999) == first
        assert keys.at(2000) == second
        with pytest.raises(ValueError):
            keys.at(999)


class TestProfile:
    @pytest.mark.parametrize(
        ('app', 'renderer'),
        [
            ('https://app.example.com', 'https://render.example.com'),
            (
                'https://app.example.com:8443',
                'https://render.example.com:9443',
            ),
        ],
        ids=['https', 'nondefault-ports'],
    )
    def test_accepts_distinct_cookie_hosts(self, app: str, renderer: str) -> None:
        profile = Profile.model_validate(
            {
                'namespace': 'test',
                'app_origin': app,
                'render_origin': renderer,
            }
        )

        assert profile.app_origin == app
        assert profile.render_origin == renderer

    @pytest.mark.parametrize(
        ('app', 'renderer'),
        [
            ('HTTPS://app.example.com', 'https://render.example.com'),
            ('https://APP.example.com', 'https://render.example.com'),
            ('https://app.example.com', 'https://RENDER.example.com'),
            ('https://app.example.com:443', 'https://render.example.com'),
            ('https://app.example.com', 'https://render.example.com:443'),
            ('https://ａｐｐ.example.com', 'https://render.example.com'),
            ('https://app.example.com?', 'https://render.example.com'),
            ('https://app.example.com#', 'https://render.example.com'),
        ],
        ids=[
            'uppercase-scheme',
            'uppercase-app-host',
            'uppercase-render-host',
            'default-app-https-port',
            'default-render-https-port',
            'unicode-host',
            'empty-query',
            'empty-fragment',
        ],
    )
    def test_rejects_origins_that_a_native_request_would_canonicalize(
        self, app: str, renderer: str
    ) -> None:
        with pytest.raises(ValidationError, match='canonical'):
            Profile.model_validate(
                {
                    'namespace': 'test',
                    'app_origin': app,
                    'render_origin': renderer,
                }
            )

    @pytest.mark.parametrize(
        ('app', 'renderer'),
        [
            ('http://app.example.com', 'https://render.example.com'),
            ('https://app.example.com', 'http://render.example.com'),
            ('https://app.example.com', 'https://app.example.com:8443'),
            ('https://user@app.example.com', 'https://render.example.com'),
            ('https://app.example.com/path', 'https://render.example.com'),
            ('https://app.example.com?query=1', 'https://render.example.com'),
            (
                'https://app.example.com#fragment',
                'https://render.example.com',
            ),
            (
                urlunsplit(('https', 'app.example.com:bad', '', '', '')),
                'https://render.example.com',
            ),
            (
                urlunsplit(('https', 'app.example.com:70000', '', '', '')),
                'https://render.example.com',
            ),
            ('http://127.0.0.1:8874', 'http://localhost:8875'),
        ],
        ids=[
            'http-app',
            'http-renderer',
            'same-cookie-host',
            'userinfo',
            'path',
            'query',
            'fragment',
            'invalid-port',
            'port-range',
            'loopback-http',
        ],
    )
    def test_rejects_insecure_or_malformed_origin_configuration(
        self, app: str, renderer: str
    ) -> None:
        with pytest.raises(ValidationError):
            Profile.model_validate(
                {
                    'namespace': 'test',
                    'app_origin': app,
                    'render_origin': renderer,
                }
            )
