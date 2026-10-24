import hashlib
import json
from datetime import UTC, datetime
from http.cookies import SimpleCookie
from uuid import uuid4

import pytest
from itsdangerous import TimestampSigner, URLSafeTimedSerializer
from pydantic import SecretBytes

from assistant_bench_arena.admission import (
    ChallengeRejected,
    InvalidToken,
    NetworkIdentity,
    TokenCodec,
    cookie_header,
    verify_challenge,
)
from assistant_bench_arena.runtime_models import (
    AdmissionClaims,
    CredentialClaims,
    KeyRing,
    KeyVersion,
)


NOW = 1_800_000_000_000
LIFETIME = 30 * 24 * 60 * 60 * 1000


@pytest.fixture
def keys() -> KeyRing:
    return KeyRing(
        versions=(KeyVersion(version='first', starts_at=0, key=SecretBytes(b'a' * 32)),)
    )


@pytest.fixture
def claims() -> CredentialClaims:
    return CredentialClaims(
        namespace='test',
        issued_at=NOW,
        expires_at=NOW + LIFETIME,
        credential='credential',
        rate_subject='lineage',
    )


@pytest.fixture
def codec(
    keys: KeyRing, monkeypatch: pytest.MonkeyPatch
) -> TokenCodec[CredentialClaims]:
    def timestamp(self: TimestampSigner) -> int:
        return NOW // 1000

    monkeypatch.setattr(TimestampSigner, 'get_timestamp', timestamp)

    return TokenCodec(CredentialClaims, keys, 'test', 'credential', LIFETIME)


def serializer(keys: KeyRing, *, purpose: str = 'credential') -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        [version.key.get_secret_value() for version in keys.versions],
        salt=f'assistant-bench:test:{purpose}:1',
        signer_kwargs={'digest_method': hashlib.sha256},
    )


class TestTokenCodec:
    def test_round_trips_claims_without_changing_their_expiration(
        self, codec: TokenCodec[CredentialClaims], claims: CredentialClaims
    ) -> None:
        token = codec.encode(claims)

        decoded = codec.decode(token, NOW + 1000)

        assert decoded == claims
        assert decoded.expires_at == NOW + LIFETIME

    @pytest.mark.parametrize(
        ('namespace', 'purpose'),
        [('another-profile', 'credential'), ('test', 'pair'), ('test', 'render')],
        ids=['profile', 'pair-purpose', 'render-purpose'],
    )
    def test_rejects_another_profile_or_purpose(
        self,
        codec: TokenCodec[CredentialClaims],
        claims: CredentialClaims,
        keys: KeyRing,
        namespace: str,
        purpose: str,
    ) -> None:
        token = codec.encode(claims)
        other = TokenCodec(CredentialClaims, keys, namespace, purpose, LIFETIME)

        with pytest.raises(InvalidToken):
            other.decode(token, NOW)

    @pytest.mark.parametrize('token', ['', 'x.y.z', 'x' * 2049])
    def test_rejects_malformed_tokens(
        self, codec: TokenCodec[CredentialClaims], token: str
    ) -> None:
        with pytest.raises(InvalidToken):
            codec.decode(token, NOW)

    def test_rejects_a_tampered_signature(
        self, codec: TokenCodec[CredentialClaims], claims: CredentialClaims
    ) -> None:
        token = codec.encode(claims)
        payload, signature = token.rsplit('.', 1)
        changed = ('a' if signature[0] != 'a' else 'b') + signature[1:]

        with pytest.raises(InvalidToken):
            codec.decode(f'{payload}.{changed}', NOW)

    @pytest.mark.parametrize(
        'payload',
        [None, [], {'credential': 'credential'}, '[]', '{"unknown":true}'],
        ids=['null', 'list', 'object', 'json-list', 'wrong-fields'],
    )
    def test_rejects_malformed_claims_even_with_a_valid_signature(
        self, codec: TokenCodec[CredentialClaims], keys: KeyRing, payload: object
    ) -> None:
        token = serializer(keys).dumps(payload)

        with pytest.raises(InvalidToken):
            codec.decode(token, NOW)

    @pytest.mark.parametrize(
        'updates',
        [
            {'namespace': 'another-profile'},
            {'issued_at': NOW + 1},
            {'expires_at': NOW},
            {'expires_at': NOW + LIFETIME + 1},
            {'expires_at': True},
            {'actor': 'private-actor'},
        ],
        ids=['profile', 'future', 'expired', 'overlong', 'boolean-time', 'extra-field'],
    )
    def test_rejects_signed_claims_outside_the_contract(
        self,
        codec: TokenCodec[CredentialClaims],
        claims: CredentialClaims,
        keys: KeyRing,
        updates: dict[str, object],
    ) -> None:
        payload = claims.model_dump(mode='json')
        payload.update(updates)
        token = serializer(keys).dumps(json.dumps(payload))

        with pytest.raises(InvalidToken):
            codec.decode(token, NOW)

    def test_enforces_absolute_expiry_and_the_bounded_recovery_window(
        self, codec: TokenCodec[CredentialClaims], claims: CredentialClaims
    ) -> None:
        token = codec.encode(claims)
        expiry = claims.expires_at

        assert codec.decode(token, expiry - 1) == claims
        with pytest.raises(InvalidToken):
            codec.decode(token, expiry)

        assert codec.decode(token, expiry + 599_999, recovery_ms=600_000) == claims
        with pytest.raises(InvalidToken):
            codec.decode(token, expiry + 600_000, recovery_ms=600_000)

    def test_rotates_signing_keys_while_retaining_old_credentials(
        self,
        codec: TokenCodec[CredentialClaims],
        claims: CredentialClaims,
        keys: KeyRing,
    ) -> None:
        newer = KeyVersion(version='second', starts_at=NOW, key=SecretBytes(b'b' * 32))
        rotated_keys = KeyRing(versions=(*keys.versions, newer))
        rotated = TokenCodec(
            CredentialClaims, rotated_keys, 'test', 'credential', LIFETIME
        )
        current_keys = KeyRing(versions=(newer,))
        current = TokenCodec(
            CredentialClaims, current_keys, 'test', 'credential', LIFETIME
        )
        old_token = codec.encode(claims)

        new_token = rotated.encode(claims)

        assert rotated.decode(old_token, NOW) == claims
        assert current.decode(new_token, NOW) == claims
        with pytest.raises(InvalidToken):
            codec.decode(new_token, NOW)
        with pytest.raises(InvalidToken):
            current.decode(old_token, NOW)


class TestNetworkIdentity:
    @pytest.mark.parametrize(
        ('first', 'second'),
        [
            ('192.0.2.1', '::ffff:192.0.2.1'),
            ('2001:0db8:0001:0000::1', '2001:db8:1::1'),
        ],
        ids=['mapped-ipv4', 'compressed-ipv6'],
    )
    def test_canonicalizes_equal_addresses_to_equal_tags(
        self, keys: KeyRing, first: str, second: str
    ) -> None:
        a, b = NetworkIdentity(first), NetworkIdentity(second)
        version = keys.versions[0]

        assert a.address == b.address
        assert a.attribution(version, 'test', 'exact') == b.attribution(
            version, 'test', 'exact'
        )
        assert a.quota(keys, 'test', 'accept', 'exact', 0, 60_000) == b.quota(
            keys, 'test', 'accept', 'exact', 0, 60_000
        )

    @pytest.mark.parametrize(
        ('first', 'inside', 'outside'),
        [
            ('192.0.2.1', '192.0.2.254', '192.0.3.1'),
            ('2001:db8:abcd:1200::1', '2001:db8:abcd:12ff::1', '2001:db8:abcd:1300::1'),
        ],
        ids=['ipv4-24', 'ipv6-56'],
    )
    def test_groups_only_the_selected_network_prefix(
        self, keys: KeyRing, first: str, inside: str, outside: str
    ) -> None:
        a, b, c = map(NetworkIdentity, (first, inside, outside))
        version = keys.versions[0]

        assert a.attribution(version, 'test', 'prefix') == b.attribution(
            version, 'test', 'prefix'
        )
        assert a.attribution(version, 'test', 'prefix') != c.attribution(
            version, 'test', 'prefix'
        )
        assert a.attribution(version, 'test', 'exact') != b.attribution(
            version, 'test', 'exact'
        )

    @pytest.mark.parametrize(
        'address', ['', 'invalid', '192.0.2.999', '2a06:98c0:3600::103']
    )
    def test_rejects_invalid_or_cross_zone_ingress(self, address: str) -> None:
        with pytest.raises(ValueError):
            NetworkIdentity(address)

    @pytest.mark.parametrize(
        ('namespace', 'operation', 'scope', 'window_start', 'window_ms'),
        [
            ('other', 'accept', 'exact', 0, 60_000),
            ('test', 'issue', 'exact', 0, 60_000),
            ('test', 'accept', 'prefix', 0, 60_000),
            ('test', 'accept', 'exact', 60_000, 60_000),
            ('test', 'accept', 'exact', 0, 86_400_000),
        ],
        ids=['deployment', 'operation', 'scope', 'window-start', 'window-size'],
    )
    def test_separates_quota_domains(
        self,
        keys: KeyRing,
        namespace: str,
        operation: str,
        scope: str,
        window_start: int,
        window_ms: int,
    ) -> None:
        address = NetworkIdentity('192.0.2.1')
        ordinary = address.quota(keys, 'test', 'accept', 'exact', 0, 60_000)

        other = address.quota(
            keys, namespace, operation, scope, window_start, window_ms
        )

        assert ordinary != other
        assert ordinary != address.attribution(keys.versions[0], 'test', 'exact')

    def test_rotation_preserves_an_open_window_and_changes_the_next_epoch(
        self, keys: KeyRing
    ) -> None:
        address = NetworkIdentity('192.0.2.1')
        rotated = KeyRing(
            versions=(
                *keys.versions,
                KeyVersion(
                    version='second', starts_at=60_000, key=SecretBytes(b'b' * 32)
                ),
            )
        )
        before = address.quota(keys, 'test', 'accept', 'exact', 0, 60_000)

        assert address.quota(rotated, 'test', 'accept', 'exact', 0, 60_000) == before
        assert (
            address.quota(rotated, 'test', 'accept', 'exact', 60_000, 60_000) != before
        )


class TestVerifyChallenge:
    @pytest.fixture
    def context(self) -> AdmissionClaims:
        return AdmissionClaims(
            namespace='test',
            issued_at=NOW,
            expires_at=NOW + 600_000,
            flow=uuid4(),
            operation='new',
        )

    @pytest.fixture
    def response(self, context: AdmissionClaims) -> dict[str, object]:
        return {
            'success': True,
            'challenge_ts': datetime.fromtimestamp(NOW / 1000, UTC).isoformat(),
            'hostname': 'app.example.com',
            'action': 'bench_admit',
            'cdata': str(context.flow),
        }

    @pytest.mark.parametrize('age_ms', [0, 299_999, 300_000])
    def test_accepts_the_exact_verified_flow_within_its_lifetime(
        self, context: AdmissionClaims, response: dict[str, object], age_ms: int
    ) -> None:
        body = json.dumps(response).encode()

        verify_challenge(body, context, 'app.example.com', NOW + age_ms)

    def test_binds_renewal_to_its_separate_provider_action(
        self, context: AdmissionClaims, response: dict[str, object]
    ) -> None:
        renewal = context.model_copy(
            update={'operation': 'renew', 'original_credential': 'credential'}
        )
        original_body = json.dumps(response).encode()
        response['action'] = 'bench_renew'

        with pytest.raises(ChallengeRejected):
            verify_challenge(original_body, renewal, 'app.example.com', NOW)
        verify_challenge(json.dumps(response).encode(), renewal, 'app.example.com', NOW)

    @pytest.mark.parametrize(
        'updates',
        [
            {'success': False},
            {'success': 'true'},
            {'success': 1},
            {'hostname': 'other.example.com'},
            {'action': 'bench_renew'},
            {'cdata': 'another-flow'},
            {'challenge_ts': 'invalid'},
            {
                'challenge_ts': datetime.fromtimestamp(NOW / 1000, UTC)
                .replace(tzinfo=None)
                .isoformat()
            },
        ],
        ids=[
            'failure',
            'string-boolean',
            'integer-boolean',
            'host',
            'action',
            'flow',
            'invalid-time',
            'naive-time',
        ],
    )
    def test_rejects_an_unverified_or_mismatched_result(
        self,
        context: AdmissionClaims,
        response: dict[str, object],
        updates: dict[str, object],
    ) -> None:
        response.update(updates)
        body = json.dumps(response).encode()

        with pytest.raises(ChallengeRejected):
            verify_challenge(body, context, 'app.example.com', NOW)

    @pytest.mark.parametrize('age_ms', [-1, 300_001])
    def test_rejects_future_or_old_provider_timestamps(
        self, context: AdmissionClaims, response: dict[str, object], age_ms: int
    ) -> None:
        body = json.dumps(response).encode()

        with pytest.raises(ChallengeRejected):
            verify_challenge(body, context, 'app.example.com', NOW + age_ms)

    @pytest.mark.parametrize('body', [b'invalid', b'[]', b'null', b' ' * 8193])
    def test_rejects_malformed_or_oversized_provider_data(
        self, context: AdmissionClaims, body: bytes
    ) -> None:
        with pytest.raises(ChallengeRejected):
            verify_challenge(body, context, 'app.example.com', NOW)


class TestCookieHeader:
    @pytest.mark.parametrize('secure', [False, True], ids=['local', 'public'])
    def test_preserves_host_only_http_only_cookie_policy(self, secure: bool) -> None:
        header = cookie_header(
            'credential', 'synthetic', NOW + 60_000, NOW, secure=secure
        )
        cookie = SimpleCookie()

        cookie.load(header)

        value = cookie['credential']
        assert value.value == 'synthetic'
        assert value['path'] == '/'
        assert value['httponly']
        assert value['samesite'] == 'Strict'
        assert not value['domain']
        assert bool(value['secure']) == secure

    def test_replay_uses_the_original_expiration_and_remaining_lifetime(self) -> None:
        initial = SimpleCookie()
        replay = SimpleCookie()
        initial.load(
            cookie_header('credential', 'synthetic', NOW + 60_000, NOW, secure=True)
        )

        replay.load(
            cookie_header(
                'credential', 'synthetic', NOW + 60_000, NOW + 10_000, secure=True
            )
        )

        assert initial['credential']['expires'] == replay['credential']['expires']
        assert initial['credential']['max-age'] == '60'
        assert replay['credential']['max-age'] == '50'
