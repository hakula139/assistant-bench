"""Endpoint-bound clients and authentication for the bench's model APIs."""

import json
import os
import ssl
from collections.abc import Callable, Mapping
from typing import Any, Literal, override

import h11
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
    TypeAdapter,
    ValidationError,
    field_validator,
)

from assistant_bench.catalog import EXPORT_VAR, Api
from assistant_bench.generation import load_generation
from assistant_bench.harnesses.accounting import (
    REQUEST_ID,
    RequestReceipt,
    TrialAccounting,
    current_trial,
)
from assistant_bench.harnesses.usage import measured_usage
from assistant_bench.messages import code


BASE_URL_VAR = 'BENCH_API_BASE_URL'
ANTHROPIC_BASE_URL_VAR = 'BENCH_ANTHROPIC_BASE_URL'
KEY_FILE_VAR = 'BENCH_API_KEY_FILE'
CA_FILE_VAR = 'BENCH_API_CA_FILE'
HEADERS_VAR = 'BENCH_API_HEADERS'
SECRET_HEADERS_FILE_VAR = 'BENCH_API_SECRET_HEADERS_FILE'
LOCAL_ADDRESS_VAR = 'BENCH_API_LOCAL_ADDRESS'
ENDPOINT_VARS = (
    BASE_URL_VAR,
    ANTHROPIC_BASE_URL_VAR,
    KEY_FILE_VAR,
    CA_FILE_VAR,
    HEADERS_VAR,
    SECRET_HEADERS_FILE_VAR,
    LOCAL_ADDRESS_VAR,
)

# The Anthropic SDK adds these headers to every request it sends.
SDK_HEADERS_VAR = 'ANTHROPIC_CUSTOM_HEADERS'

REQUEST_TIMEOUT = httpx2.Timeout(600.0, connect=30.0, read=None)
_payload = TypeAdapter(dict[str, Any])
_choices = TypeAdapter(list[dict[str, Any]])
_headers = TypeAdapter(dict[str, str], config=ConfigDict(hide_input_in_errors=True))
_AUTH_HEADERS = frozenset(('authorization', 'proxy-authorization', 'x-api-key'))


class HeaderSettings(BaseModel):
    """Configured headers and the explicit boundary for confidential values."""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    headers: dict[str, str] | None = Field(default=None, validation_alias=HEADERS_VAR)
    secret_headers_file: FilePath | None = Field(
        default=None, validation_alias=SECRET_HEADERS_FILE_VAR
    )

    @field_validator('headers', mode='before')
    @classmethod
    def _parse_headers(cls, value: object) -> dict[str, str] | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError('Extra headers must be a JSON object of string values.')
        return _parse_headers(value)

    @classmethod
    def from_env(cls, env: Callable[[str], str | None]) -> HeaderSettings:
        """Read header settings without requiring an endpoint or API."""
        values = {
            name: value
            for name in (HEADERS_VAR, SECRET_HEADERS_FILE_VAR)
            if (value := env(name))
        }
        return cls.model_validate(values)

    def resolve(self) -> tuple[dict[str, str], frozenset[str]]:
        """Read confidential headers and return one normalized configuration."""
        secret_headers: dict[str, str] = {}
        if self.secret_headers_file:
            try:
                content = self.secret_headers_file.read_bytes()
            except OSError:
                raise ValueError(
                    'The confidential header file could not be read.'
                ) from None
            secret_headers = _parse_headers(content)
        try:
            ordinary = httpx2.Headers(self.headers or {})
            confidential = httpx2.Headers(secret_headers)
            # HTTP/1.0 validates configured headers without requiring Host.
            h11.Request(
                method=b'POST',
                target=b'/',
                headers=[*ordinary.raw, *confidential.raw],
                http_version=b'1.0',
            )
        except h11.LocalProtocolError, UnicodeError, TypeError:
            raise ValueError(
                'Extra headers must have valid names and values.'
            ) from None
        if ordinary.keys() & _AUTH_HEADERS:
            raise ValueError(
                'Authentication headers require the confidential header file.'
            )
        if ordinary.keys() & confidential.keys():
            raise ValueError('Header names must be unique ignoring case.')
        headers = dict(ordinary)
        headers.update(confidential)
        return headers, frozenset(confidential)


class Endpoint(HeaderSettings):
    """The endpoint variables, where an empty value counts as unset."""

    base_url: HttpUrl = Field(
        validation_alias=AliasChoices(BASE_URL_VAR, ANTHROPIC_BASE_URL_VAR)
    )

    key_file: FilePath = Field(validation_alias=KEY_FILE_VAR)
    ca_file: FilePath | None = Field(default=None, validation_alias=CA_FILE_VAR)

    local_address: IPvAnyAddress | None = Field(
        default=None, validation_alias=LOCAL_ADDRESS_VAR
    )

    @classmethod
    def read(cls, env: Callable[[str], str | None], api: Api) -> Endpoint:
        """Validate the value `env` returns for each endpoint variable."""
        names = (
            _base_var(api),
            KEY_FILE_VAR,
            CA_FILE_VAR,
            HEADERS_VAR,
            SECRET_HEADERS_FILE_VAR,
            LOCAL_ADDRESS_VAR,
        )
        values = {name: value for name in names if (value := env(name))}
        return cls.model_validate(values)

    def api_key(self) -> str:
        return self.key_file.read_text().strip()

    def verify(self) -> ssl.SSLContext | bool:
        """Trust the CA bundle when one is set, else the system's."""
        return ssl.create_default_context(cafile=self.ca_file) if self.ca_file else True

    def client(
        self, api: Literal['openai-chat-completions', 'openai-responses']
    ) -> AsyncOpenAI:
        """Build the native client, bound to the local address if set."""
        client = self.http_client(api)
        return AsyncOpenAI(
            base_url=str(self.base_url),
            api_key=self.api_key(),
            max_retries=0,
            timeout=REQUEST_TIMEOUT,
            default_headers=client.configured_headers,
            http_client=client,
        )

    def http_client(
        self, api: Api, *, recorder: TrialAccounting | None = None
    ) -> _Client:
        """Use the explicit recorder or the current trial for every HTTP request."""
        verify = self.verify()
        transport = (
            httpx2.AsyncHTTPTransport(
                local_address=str(self.local_address), verify=verify
            )
            if self.local_address
            else None
        )

        return _Client(
            api,
            self,
            recorder,
            transport=transport,
            verify=verify,
            follow_redirects=False,
            timeout=REQUEST_TIMEOUT,
        )

    def messages_client(self) -> AsyncAnthropic:
        client = self.http_client('anthropic-messages')
        return AsyncAnthropic(
            base_url=str(self.base_url),
            api_key=self.api_key(),
            max_retries=0,
            open_telemetry=False,
            timeout=REQUEST_TIMEOUT,
            default_headers={
                **client.configured_headers,
                'anthropic-version': load_generation().anthropic_api_version,
            },
            http_client=client,
        )


class _Client(httpx2.AsyncClient):
    def __init__(
        self,
        api: Api,
        endpoint: Endpoint,
        recorder: TrialAccounting | None,
        **kwargs: Any,
    ) -> None:
        self._configured_headers, self._confidential_headers = endpoint.resolve()
        super().__init__(
            event_hooks={'request': [self._admit], 'response': [self._confirm]},
            **kwargs,
        )
        self._api: Api = api
        self._recorder = recorder

    @property
    def configured_headers(self) -> dict[str, str]:
        return self._configured_headers.copy()

    def _accounting(self) -> TrialAccounting:
        return self._recorder if self._recorder is not None else current_trial()

    async def _admit(self, request: httpx2.Request) -> None:
        recorder = self._accounting()
        for name in _AUTH_HEADERS | self._confidential_headers:
            if value := request.headers.get(name):
                recorder.remember_secret(value)
                if name in ('authorization', 'proxy-authorization'):
                    scheme, separator, credential = value.partition(' ')
                    if separator and scheme.lower() == 'bearer':
                        recorder.remember_secret(credential)
        if request.method != 'POST' or request.url.path.endswith('/count_tokens'):
            return
        request.extensions[REQUEST_ID] = await recorder.admit(self._api)

    async def _confirm(self, response: httpx2.Response) -> None:
        request_id = response.request.extensions.get(REQUEST_ID)
        if not isinstance(request_id, str):
            return
        if (
            response.is_success
            and response.headers.get('content-type', '')
            .split(';', 1)[0]
            .strip()
            .lower()
            == 'text/event-stream'
        ):
            return
        await response.aread()
        try:
            payload = response.json()
        except ValueError:
            payload = None
        await self._accounting().confirm(
            request_id,
            receipt(
                self._api,
                payload,
                response.status_code,
                response.headers.get('x-request-id'),
            ),
        )

    @override
    async def send(self, request: httpx2.Request, **kwargs: Any) -> httpx2.Response:
        try:
            return await super().send(request, **kwargs)
        except BaseException:
            if REQUEST_ID in request.extensions:
                await self._accounting().uncertain()
            raise


def _parse_headers(value: str | bytes) -> dict[str, str]:
    try:
        values = json.loads(value, object_pairs_hook=_unique_headers)
    except json.JSONDecodeError, UnicodeDecodeError:
        raise ValueError(
            'Extra headers must be a JSON object of string values.'
        ) from None
    try:
        return _headers.validate_python(values)
    except ValidationError:
        raise ValueError(
            'Extra headers must be a JSON object of string values.'
        ) from None


def _unique_headers(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    names: set[str] = set()
    values: dict[str, Any] = {}
    for name, value in pairs:
        if name.lower() in names:
            raise ValueError('Header names must be unique ignoring case.')
        names.add(name.lower())
        values[name] = value
    return values


def receipt(
    api: Api,
    payload: object,
    http_status: int,
    provider_request_id: str | None,
) -> RequestReceipt:
    """Validate terminal metadata without retaining response text or headers."""
    outcome: Literal['completed', 'incomplete', 'failed', 'http_error', 'malformed'] = (
        'completed'
    )
    try:
        values = _payload.validate_python(payload)
    except ValidationError:
        values = {}
        outcome = 'malformed'
    if http_status >= 400:
        outcome = 'http_error'
    elif values.get('error') is not None:
        outcome = 'failed'
    elif api == 'openai-responses':
        status = values.get('status')
        outcome = (
            status if status in ('completed', 'incomplete', 'failed') else 'malformed'
        )
    elif api == 'openai-chat-completions':
        try:
            choices = _choices.validate_python(values.get('choices'))
        except ValidationError:
            choices = []
        if not choices:
            outcome = 'malformed'
        elif any(choice.get('finish_reason') == 'length' for choice in choices):
            outcome = 'incomplete'
    else:
        if values.get('stop_reason') == 'max_tokens':
            outcome = 'incomplete'
        elif values.get('type') != 'message':
            outcome = 'malformed'
    return RequestReceipt(
        api=api,
        outcome=outcome,
        http_status=http_status,
        usage=measured_usage(api, values.get('usage')),
        response_model=values.get('model')
        if isinstance(values.get('model'), str)
        else None,
        response_id=values.get('id') if isinstance(values.get('id'), str) else None,
        provider_request_id=provider_request_id,
    )


def preflight(env: Mapping[str, str] | None, api: Api) -> None:
    """Raise unless the endpoint variables and the nixos-config export are set.

    The endpoint must also resolve to a usable one. `env` overrides the process
    environment, as an agent's `env` does.
    """
    overrides = env or {}

    def lookup(var: str) -> str | None:
        return overrides.get(var, os.environ.get(var))

    if api == 'anthropic-messages' and lookup(SDK_HEADERS_VAR):
        raise ValueError(
            f'Unset `{SDK_HEADERS_VAR}`. Configure native endpoint headers '
            f'with `{HEADERS_VAR}`.'
        )

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
