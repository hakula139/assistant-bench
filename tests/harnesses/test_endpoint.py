import ssl
from pathlib import Path

import pytest
from pydantic import ValidationError

from assistant_bench.catalog import EXPORT_VAR
from assistant_bench.harnesses.endpoint import (
    BASE_URL_VAR,
    CA_FILE_VAR,
    HEADERS_VAR,
    KEY_FILE_VAR,
    LOCAL_ADDRESS_VAR,
    preflight,
)
from support import StubEndpoint


pytestmark = pytest.mark.usefixtures('export')


class TestPreflight:
    def test_accepts_endpoint_variables_from_the_agent_env(
        self, endpoint: StubEndpoint
    ) -> None:
        preflight(endpoint.env)

    def test_names_each_missing_variable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(EXPORT_VAR)

        with pytest.raises(ValueError) as error:
            preflight({})

        assert str(error.value) == (
            'BENCH_API_BASE_URL, BENCH_API_KEY_FILE, BENCH_NIXOS_CONFIG unset. '
            'Copy .env.example to .env, and run inside `nix develop`.'
        )

    @pytest.mark.parametrize(
        ('variable', 'value'),
        [
            (BASE_URL_VAR, 'api.example.com/v1'),
            (KEY_FILE_VAR, 'missing-key'),
            (HEADERS_VAR, '{"x-trace": 1}'),
            (CA_FILE_VAR, 'missing-ca.pem'),
            (LOCAL_ADDRESS_VAR, 'eth0'),
        ],
    )
    def test_rejects_an_unusable_endpoint_setting(
        self, endpoint: StubEndpoint, variable: str, value: str
    ) -> None:
        with pytest.raises(ValidationError) as error:
            preflight(endpoint.env | {variable: value})

        assert [e['loc'][0] for e in error.value.errors()] == [variable]
        assert value not in str(error.value)

    def test_rejects_a_ca_bundle_that_is_not_pem(
        self, endpoint: StubEndpoint, tmp_path: Path
    ) -> None:
        ca_file = tmp_path / 'ca.pem'
        ca_file.write_text('not a certificate\n')

        with pytest.raises(ssl.SSLError):
            preflight(endpoint.env | {CA_FILE_VAR: str(ca_file)})

    def test_treats_an_empty_optional_setting_as_unset(
        self, endpoint: StubEndpoint
    ) -> None:
        empty = dict.fromkeys((CA_FILE_VAR, HEADERS_VAR, LOCAL_ADDRESS_VAR), '')

        preflight(endpoint.env | empty)
