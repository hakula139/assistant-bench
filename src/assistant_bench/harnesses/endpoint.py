"""Endpoint-bound clients and authentication for the bench's model APIs."""

import os
import ssl
from collections.abc import Callable, Mapping

import httpx
import httpx2
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    FilePath,
    HttpUrl,
    IPvAnyAddress,
    Json,
)

from assistant_bench.catalog import EXPORT_VAR, Api
from assistant_bench.generation import load_generation
from assistant_bench.messages import code


BASE_URL_VAR = 'BENCH_API_BASE_URL'
ANTHROPIC_BASE_URL_VAR = 'BENCH_ANTHROPIC_BASE_URL'
KEY_FILE_VAR = 'BENCH_API_KEY_FILE'
CA_FILE_VAR = 'BENCH_API_CA_FILE'
HEADERS_VAR = 'BENCH_API_HEADERS'
LOCAL_ADDRESS_VAR = 'BENCH_API_LOCAL_ADDRESS'
ENDPOINT_VARS = (
    BASE_URL_VAR,
    ANTHROPIC_BASE_URL_VAR,
    KEY_FILE_VAR,
    CA_FILE_VAR,
    HEADERS_VAR,
    LOCAL_ADDRESS_VAR,
)
# The Anthropic SDK adds these headers to every request it sends.
SDK_HEADERS_VAR = 'ANTHROPIC_CUSTOM_HEADERS'

REQUEST_TIMEOUT = httpx.Timeout(600.0, connect=30.0, read=None)
MESSAGES_TIMEOUT = httpx2.Timeout(**REQUEST_TIMEOUT.as_dict())


class Endpoint(BaseModel):
    """The endpoint variables, where an empty value counts as unset."""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    base_url: HttpUrl = Field(
        validation_alias=AliasChoices(BASE_URL_VAR, ANTHROPIC_BASE_URL_VAR)
    )

    key_file: FilePath = Field(validation_alias=KEY_FILE_VAR)
    ca_file: FilePath | None = Field(default=None, validation_alias=CA_FILE_VAR)

    headers: Json[dict[str, str]] | None = Field(
        default=None, validation_alias=HEADERS_VAR
    )

    local_address: IPvAnyAddress | None = Field(
        default=None, validation_alias=LOCAL_ADDRESS_VAR
    )

    @classmethod
    def read(
        cls, env: Callable[[str], str | None], api: Api = 'openai-chat-completions'
    ) -> Endpoint:
        """Validate the value `env` returns for each endpoint variable."""
        names = (
            _base_var(api),
            KEY_FILE_VAR,
            CA_FILE_VAR,
            HEADERS_VAR,
            LOCAL_ADDRESS_VAR,
        )
        values = {name: value for name in names if (value := env(name))}
        return cls.model_validate(values)

    def api_key(self) -> str:
        return self.key_file.read_text().strip()

    def verify(self) -> ssl.SSLContext | bool:
        """Trust the CA bundle when one is set, else the system's."""
        return ssl.create_default_context(cafile=self.ca_file) if self.ca_file else True

    def client(self) -> AsyncOpenAI:
        """Build the native client, bound to the local address if set."""
        return AsyncOpenAI(
            base_url=str(self.base_url),
            api_key=self.api_key(),
            max_retries=0,
            timeout=REQUEST_TIMEOUT,
            default_headers=self.headers,
            http_client=self.http_client(),
        )

    def http_client(self) -> httpx.AsyncClient:
        """Bind the local address and refuse automatic redirects."""
        verify = self.verify()
        transport = (
            httpx.AsyncHTTPTransport(
                local_address=str(self.local_address), verify=verify
            )
            if self.local_address
            else None
        )

        return httpx.AsyncClient(
            transport=transport,
            verify=verify,
            follow_redirects=False,
            timeout=REQUEST_TIMEOUT,
        )

    def messages_client(self) -> AsyncAnthropic:
        _check_messages_environment(os.environ.get)
        return AsyncAnthropic(
            base_url=str(self.base_url),
            api_key=self.api_key(),
            max_retries=0,
            open_telemetry=False,
            timeout=MESSAGES_TIMEOUT,
            default_headers={
                **(self.headers or {}),
                'anthropic-version': load_generation().anthropic_api_version,
            },
            http_client=self.messages_http_client(),
        )

    def messages_http_client(self) -> httpx2.AsyncClient:
        verify = self.verify()
        transport = (
            httpx2.AsyncHTTPTransport(
                local_address=str(self.local_address), verify=verify
            )
            if self.local_address
            else None
        )
        return httpx2.AsyncClient(
            transport=transport,
            verify=verify,
            follow_redirects=False,
            timeout=MESSAGES_TIMEOUT,
        )


def preflight(
    env: Mapping[str, str] | None = None, api: Api = 'openai-chat-completions'
) -> None:
    """Raise unless the endpoint variables and the nixos-config export are set.

    The endpoint must also resolve to a usable one. `env` overrides the process
    environment, as an agent's `env` does.
    """
    overrides = env or {}

    def lookup(var: str) -> str | None:
        return overrides.get(var, os.environ.get(var))

    if api == 'anthropic-messages':
        _check_messages_environment(lookup)

    required = (_base_var(api), KEY_FILE_VAR, EXPORT_VAR)
    missing = [var for var in required if not lookup(var)]
    if missing:
        raise ValueError(
            f'Set {code(missing)}. Copy `.env.example` to `.env`, and run inside '
            '`nix develop`.'
        )
    Endpoint.read(lookup, api).verify()


def _base_var(api: Api) -> str:
    return ANTHROPIC_BASE_URL_VAR if api == 'anthropic-messages' else BASE_URL_VAR


def _check_messages_environment(env: Callable[[str], str | None]) -> None:
    if env(SDK_HEADERS_VAR):
        raise ValueError(
            f'Unset `{SDK_HEADERS_VAR}`. Configure native endpoint headers '
            f'with `{HEADERS_VAR}`.'
        )
