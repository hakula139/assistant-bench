"""Harbor agent that answers a task with one chat-completions call from the host."""

import os
import ssl
import time
from collections.abc import Callable, Mapping
from importlib import metadata
from typing import Any, override

import httpx
from harbor.agents.base import BaseAgent
from harbor.agents.options import AgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FilePath,
    HttpUrl,
    IPvAnyAddress,
    Json,
)

from assistant_bench.catalog import EXPORT_VAR, load_export


OUTPUT_FILE = 'output.txt'

BASE_URL_VAR = 'BENCH_API_BASE_URL'
KEY_FILE_VAR = 'BENCH_API_KEY_FILE'
CA_FILE_VAR = 'BENCH_API_CA_FILE'
HEADERS_VAR = 'BENCH_API_HEADERS'
LOCAL_ADDRESS_VAR = 'BENCH_API_LOCAL_ADDRESS'


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
        names = (
            BASE_URL_VAR,
            KEY_FILE_VAR,
            CA_FILE_VAR,
            HEADERS_VAR,
            LOCAL_ADDRESS_VAR,
        )
        return cls.model_validate(
            {name: value for name in names if (value := env(name))}
        )

    def api_key(self) -> str:
        return self.key_file.read_text().strip()

    def verify(self) -> ssl.SSLContext | bool:
        """Trust the CA bundle when one is set, else the system's."""
        return ssl.create_default_context(cafile=self.ca_file) if self.ca_file else True


class GenerateOptions(AgentOptions):
    """The agent's `kwargs`. `system` and `fingerprint` label the trial for ingest."""

    system: str
    fingerprint: str
    effort: str | None = None
    system_prompt: str | None = None
    max_output_tokens: int


class Generate(BaseAgent):
    """Agent that answers with one chat-completions request, writing `output.txt`.

    The model name is the endpoint's model ID. HTTP errors raise, so the job's retry
    policy reruns the trial, while a reply cut off by the token limit is a valid output.
    """

    options_model = GenerateOptions

    @staticmethod
    @override
    def name() -> str:
        return 'generate'

    @override
    def version(self) -> str:
        return metadata.version('assistant-bench')

    @classmethod
    @override
    def preflight(
        cls,
        kwargs: dict[str, Any] | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        super().preflight(kwargs, env)
        merged = {**os.environ, **(env or {})}
        required = (BASE_URL_VAR, KEY_FILE_VAR, EXPORT_VAR)
        missing = [var for var in required if not merged.get(var)]
        if missing:
            raise ValueError(
                f'{", ".join(missing)} unset. '
                'Copy .env.example to .env, and run inside `nix develop`.'
            )
        Endpoint.read(merged.get).verify()

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        pass

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        assert isinstance(self.options, GenerateOptions)
        assert self.model_name is not None
        nixos_config_rev = load_export().rev
        extra_body = (
            {'reasoning_effort': self.options.effort} if self.options.effort else None
        )

        async with self._client() as client:
            started = time.monotonic()
            raw = await client.chat.completions.with_raw_response.create(
                model=self.model_name,
                messages=self._messages(instruction),
                max_completion_tokens=self.options.max_output_tokens,
                extra_body=extra_body,
            )
            latency_sec = time.monotonic() - started

        completion = raw.parse()
        choice = completion.choices[0]
        (self.logs_dir / OUTPUT_FILE).write_text(choice.message.content or '')

        usage = completion.usage
        reasoning_tokens = None
        if usage is not None:
            prompt_details = usage.prompt_tokens_details
            completion_details = usage.completion_tokens_details
            context.n_input_tokens = usage.prompt_tokens
            context.n_cache_tokens = (
                prompt_details and prompt_details.cached_tokens
            ) or 0
            context.n_output_tokens = usage.completion_tokens
            reasoning_tokens = (
                completion_details and completion_details.reasoning_tokens
            )
        context.metadata = {
            'reasoning_tokens': reasoning_tokens,
            'latency_sec': round(latency_sec, 3),
            'finish_reason': choice.finish_reason,
            'response_model': completion.model,
            'request_id': raw.headers.get('x-request-id'),
            'nixos_config_rev': nixos_config_rev,
        }

    def _messages(self, instruction: str) -> list[ChatCompletionMessageParam]:
        assert isinstance(self.options, GenerateOptions)
        user: ChatCompletionMessageParam = {'role': 'user', 'content': instruction}
        if self.options.system_prompt is None:
            return [user]
        return [{'role': 'system', 'content': self.options.system_prompt}, user]

    def _client(self) -> AsyncOpenAI:
        endpoint = Endpoint.read(self._get_env)
        verify = endpoint.verify()
        transport = (
            httpx.AsyncHTTPTransport(
                local_address=str(endpoint.local_address), verify=verify
            )
            if endpoint.local_address
            else None
        )
        return AsyncOpenAI(
            base_url=str(endpoint.base_url),
            api_key=endpoint.api_key(),
            default_headers=endpoint.headers,
            max_retries=0,
            http_client=httpx.AsyncClient(transport=transport, verify=verify),
        )
