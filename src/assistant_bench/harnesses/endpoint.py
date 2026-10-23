"""The OpenAI-compatible endpoint both agents send their model calls to."""

import os
import ssl
from collections.abc import Callable, Mapping

import httpx
from openai import AsyncOpenAI
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FilePath,
    HttpUrl,
    IPvAnyAddress,
    Json,
)

from assistant_bench.catalog import EXPORT_VAR


BASE_URL_VAR = 'BENCH_API_BASE_URL'
KEY_FILE_VAR = 'BENCH_API_KEY_FILE'
CA_FILE_VAR = 'BENCH_API_CA_FILE'
HEADERS_VAR = 'BENCH_API_HEADERS'
LOCAL_ADDRESS_VAR = 'BENCH_API_LOCAL_ADDRESS'
ENDPOINT_VARS = (
    BASE_URL_VAR,
    KEY_FILE_VAR,
    CA_FILE_VAR,
    HEADERS_VAR,
    LOCAL_ADDRESS_VAR,
)


class Endpoint(BaseModel):
    """The endpoint variables, where an empty value counts as unset."""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    base_url: HttpUrl = Field(validation_alias=BASE_URL_VAR)
    key_file: FilePath = Field(validation_alias=KEY_FILE_VAR)
    ca_file: FilePath | None = Field(default=None, validation_alias=CA_FILE_VAR)
    headers: Json[dict[str, str]] | None = Field(
        default=None, validation_alias=HEADERS_VAR
    )
    local_address: IPvAnyAddress | None = Field(
        default=None, validation_alias=LOCAL_ADDRESS_VAR
    )

    @classmethod
    def read(cls, env: Callable[[str], str | None]) -> Endpoint:
        """Validate the value `env` returns for each endpoint variable."""
        return cls.model_validate(
            {name: value for name in ENDPOINT_VARS if (value := env(name))}
        )

    def api_key(self) -> str:
        return self.key_file.read_text().strip()

    def verify(self) -> ssl.SSLContext | bool:
        """Trust the CA bundle when one is set, else the system's."""
        return ssl.create_default_context(cafile=self.ca_file) if self.ca_file else True

    def client(self) -> AsyncOpenAI:
        """Build the chat-completions client, bound to the local address if set."""
        verify = self.verify()
        transport = (
            httpx.AsyncHTTPTransport(
                local_address=str(self.local_address), verify=verify
            )
            if self.local_address
            else None
        )
        return AsyncOpenAI(
            base_url=str(self.base_url),
            api_key=self.api_key(),
            default_headers=self.headers,
            max_retries=0,
            http_client=httpx.AsyncClient(transport=transport, verify=verify),
        )


def preflight(env: Mapping[str, str] | None = None) -> None:
    """Raise unless the endpoint variables and the nixos-config export are set.

    The endpoint must also resolve to a usable one. `env` overrides the process
    environment, as an agent's `env` does.
    """
    merged = {**os.environ, **(env or {})}
    required = (BASE_URL_VAR, KEY_FILE_VAR, EXPORT_VAR)
    missing = [var for var in required if not merged.get(var)]
    if missing:
        raise ValueError(
            f'{", ".join(missing)} unset. '
            'Copy .env.example to .env, and run inside `nix develop`.'
        )
    Endpoint.read(merged.get).verify()
