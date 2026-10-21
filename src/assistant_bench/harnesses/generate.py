"""Harbor agent that answers a task with one chat-completions call from the host."""

import os
import ssl
import time
from collections.abc import Mapping
from importlib import metadata
from pathlib import Path
from typing import Any, override

import httpx
from harbor.agents.base import BaseAgent
from harbor.agents.options import AgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from pydantic import TypeAdapter

from assistant_bench.catalog import EXPORT_VAR, load_export


OUTPUT_FILE = 'output.txt'

BASE_URL_VAR = 'BENCH_API_BASE_URL'
KEY_FILE_VAR = 'BENCH_API_KEY_FILE'
CA_FILE_VAR = 'BENCH_API_CA_FILE'
HEADERS_VAR = 'BENCH_API_HEADERS'
LOCAL_ADDRESS_VAR = 'BENCH_API_LOCAL_ADDRESS'

_HEADERS = TypeAdapter(dict[str, str])


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
        _api_key(merged[KEY_FILE_VAR])
        _headers(merged.get(HEADERS_VAR))
        _verify(merged.get(CA_FILE_VAR))

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
        completion_details = usage and usage.completion_tokens_details
        if usage is not None:
            prompt_details = usage.prompt_tokens_details
            context.n_input_tokens = usage.prompt_tokens
            context.n_cache_tokens = (
                prompt_details and prompt_details.cached_tokens
            ) or 0
            context.n_output_tokens = usage.completion_tokens
        context.metadata = {
            'reasoning_tokens': completion_details
            and completion_details.reasoning_tokens,
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
        base_url = self._get_env(BASE_URL_VAR)
        key_file = self._get_env(KEY_FILE_VAR)
        assert base_url and key_file
        verify = _verify(self._get_env(CA_FILE_VAR))
        local_address = self._get_env(LOCAL_ADDRESS_VAR)
        transport = (
            httpx.AsyncHTTPTransport(local_address=local_address, verify=verify)
            if local_address
            else None
        )
        return AsyncOpenAI(
            base_url=base_url,
            api_key=_api_key(key_file),
            default_headers=_headers(self._get_env(HEADERS_VAR)),
            max_retries=0,
            http_client=httpx.AsyncClient(transport=transport, verify=verify),
        )


def _api_key(key_file: str) -> str:
    return Path(key_file).read_text().strip()


def _headers(headers: str | None) -> dict[str, str] | None:
    return _HEADERS.validate_json(headers) if headers else None


def _verify(ca_file: str | None) -> ssl.SSLContext | bool:
    return ssl.create_default_context(cafile=ca_file) if ca_file else True
