import os
from pathlib import Path
from textwrap import dedent
from typing import Any

import httpx
import pytest

from assistant_bench.arena import d1
from assistant_bench.messages import InputError


ACCOUNT = 'synthetic-account'
DATABASE = 'synthetic-database'
PRIVATE_DATABASE = 'private-database'


class TestConnect:
    @pytest.fixture
    def config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        path = tmp_path / 'wrangler.toml'
        path.write_text(
            dedent(f"""
                name = "preview"
                account_id = "{ACCOUNT}"

                [[env.staging.d1_databases]]
                binding = "DB"
                database_name = "arena-staging"
                database_id = "{DATABASE}"

                [[env.production.d1_databases]]
                binding = "DB"
                database_name = "arena-production"

                [[env.private.d1_databases]]
                binding = "DB"
                database_name = "arena-private"
                database_id = "{PRIVATE_DATABASE}"
            """)
        )
        monkeypatch.setattr(d1, 'WRANGLER_CONFIG', path)
        return path

    @pytest.mark.parametrize(
        ('variable', 'content', 'expected'),
        [
            (d1.TOKEN_FILE_VAR, 'synthetic-token\n', 'synthetic-token'),
            (
                d1.ENV_FILE_VAR,
                'CLOUDFLARE_API_TOKEN="synthetic-${TOKEN_SUFFIX}"\n'
                'D1_UNRELATED_SETTING=from-file\n'
                'D1_ABSENT_SETTING=from-file\n',
                'synthetic-${TOKEN_SUFFIX}',
            ),
        ],
        ids=['token-file', 'dotenv-file'],
    )
    @pytest.mark.parametrize('target', ['staging', 'private'])
    def test_queries_with_only_the_token_from_its_credential_file(
        self,
        config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        variable: str,
        content: str,
        expected: str,
        target: d1.TargetName,
    ) -> None:
        token = tmp_path / 'd1-token'
        token.write_text(content)
        monkeypatch.setenv(variable, str(token))
        monkeypatch.setenv('CLOUDFLARE_API_TOKEN', 'ambient-token')
        monkeypatch.setenv('TOKEN_SUFFIX', 'expanded')
        monkeypatch.setenv('D1_UNRELATED_SETTING', 'from-process')
        monkeypatch.delenv('D1_ABSENT_SETTING', raising=False)
        requests: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={'success': True, 'result': [{}]})

        client = httpx.Client

        def mocked(**kwargs: Any) -> httpx.Client:
            return client(transport=httpx.MockTransport(handle), **kwargs)

        monkeypatch.setattr(httpx, 'Client', mocked)

        with d1.connect(target) as database:
            database.execute('SELECT 1')

        (request,) = requests
        assert request.headers['Authorization'] == f'Bearer {expected}'
        identifier = PRIVATE_DATABASE if target == 'private' else DATABASE
        assert request.url.path == (
            f'/client/v4/accounts/{ACCOUNT}/d1/database/{identifier}/query'
        )
        assert os.environ['CLOUDFLARE_API_TOKEN'] == 'ambient-token'
        assert os.environ['D1_UNRELATED_SETTING'] == 'from-process'
        assert 'D1_ABSENT_SETTING' not in os.environ

    def test_names_the_create_command_for_a_target_without_an_id(
        self, config: Path
    ) -> None:
        with pytest.raises(ValueError) as raised, d1.connect('production'):
            pass

        assert str(raised.value) == (
            'Target `production` has no D1 database ID in `web/wrangler.toml`. '
            'Create it with `wrangler d1 create arena-production` and record its '
            'ID there.'
        )

    @pytest.mark.parametrize('conflicting', [False, True], ids=['missing', 'both'])
    def test_requires_exactly_one_file_source(
        self, config: Path, monkeypatch: pytest.MonkeyPatch, conflicting: bool
    ) -> None:
        monkeypatch.setenv('CLOUDFLARE_API_TOKEN', 'ambient-token')
        if conflicting:
            monkeypatch.setenv(d1.TOKEN_FILE_VAR, 'token-file')
            monkeypatch.setenv(d1.ENV_FILE_VAR, 'dotenv-file')

        with pytest.raises(ValueError) as raised, d1.connect('staging'):
            pass

        assert str(raised.value) == (
            f'Set exactly one of `{d1.TOKEN_FILE_VAR}` and `{d1.ENV_FILE_VAR}` in '
            '`.env` to the path of a Cloudflare API token file or a dotenv '
            'file containing `CLOUDFLARE_API_TOKEN` with D1 Edit.'
        )

    @pytest.mark.parametrize('variable', [d1.TOKEN_FILE_VAR, d1.ENV_FILE_VAR])
    def test_names_a_credential_file_that_cannot_be_read(
        self,
        config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        variable: str,
    ) -> None:
        token = tmp_path / 'd1-token'
        monkeypatch.setenv(variable, str(token))

        with pytest.raises(ValueError) as raised, d1.connect('staging'):
            pass

        assert str(raised.value) == (
            f'`{variable}` names `{token}`, which cannot be read: '
            'No such file or directory.'
        )

    @pytest.mark.parametrize(
        ('variable', 'content', 'expected'),
        [
            (d1.TOKEN_FILE_VAR, '', 'a nonempty Cloudflare API token'),
            (d1.TOKEN_FILE_VAR, ' \n', 'a nonempty Cloudflare API token'),
            (
                d1.ENV_FILE_VAR,
                'UNRELATED_TOKEN=synthetic-token\n',
                'a nonempty `CLOUDFLARE_API_TOKEN`',
            ),
            (
                d1.ENV_FILE_VAR,
                'CLOUDFLARE_API_TOKEN\n',
                'a nonempty `CLOUDFLARE_API_TOKEN`',
            ),
            (
                d1.ENV_FILE_VAR,
                'CLOUDFLARE_API_TOKEN=" "\n',
                'a nonempty `CLOUDFLARE_API_TOKEN`',
            ),
        ],
        ids=['empty-file', 'blank-file', 'missing-key', 'bare-key', 'blank-value'],
    )
    def test_refuses_a_file_without_a_token(
        self,
        config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        variable: str,
        content: str,
        expected: str,
    ) -> None:
        token = tmp_path / 'd1-token'
        token.write_text(content)
        monkeypatch.setenv(variable, str(token))
        monkeypatch.setenv('CLOUDFLARE_API_TOKEN', 'ambient-token')

        with pytest.raises(ValueError) as raised, d1.connect('staging'):
            pass

        assert str(raised.value) == (
            f'`{variable}` names `{token}`, which needs {expected}.'
        )

    @pytest.mark.parametrize('variable', [d1.TOKEN_FILE_VAR, d1.ENV_FILE_VAR])
    @pytest.mark.parametrize(
        'separator',
        ['\n', '\r', '\t', '\x00', '\x7f', '\N{LATIN SMALL LETTER E WITH ACUTE}'],
        ids=['newline', 'carriage-return', 'tab', 'nul', 'delete', 'non-ascii'],
    )
    def test_refuses_unsafe_header_characters_without_exposing_the_token(
        self,
        config: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        variable: str,
        separator: str,
    ) -> None:
        token = f'synthetic-secret{separator}second'
        content = (
            f'CLOUDFLARE_API_TOKEN="{token}"\n'
            if variable == d1.ENV_FILE_VAR
            else token
        )
        path = tmp_path / 'd1-token'
        path.write_text(content)
        monkeypatch.setenv(variable, str(path))

        def unopened(**kwargs: Any) -> httpx.Client:
            pytest.fail('A client opened for an invalid token')

        monkeypatch.setattr(httpx, 'Client', unopened)

        with pytest.raises(InputError) as raised, d1.connect('staging'):
            pass

        assert str(raised.value) == (
            f'`{variable}` names `{path}`, whose token contains characters that '
            'cannot be used in an HTTP header.'
        )
        assert token not in str(raised.value)
        assert raised.value.__cause__ is None
