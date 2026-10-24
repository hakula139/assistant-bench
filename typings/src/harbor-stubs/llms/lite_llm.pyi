"""Consumed LiteLLM interfaces from the locked Harbor package."""

from pathlib import Path
from typing import Any

from harbor.llms.base import BaseLLM, LLMResponse
from litellm import Message
from pydantic import BaseModel

class LiteLLM(BaseLLM):
    def __init__(
        self,
        model_name: str,
        temperature: float | None = ...,
        api_base: str | None = ...,
        session_id: str | None = ...,
        collect_rollout_details: bool = ...,
        max_thinking_tokens: int | None = ...,
        reasoning_effort: str | None = ...,
        model_info: dict[str, Any] | None = ...,
        use_responses_api: bool = ...,
        **kwargs: Any,
    ) -> None: ...
    def get_model_context_limit(self) -> int: ...
    def get_model_output_limit(self) -> int | None: ...
    async def call(
        self,
        prompt: str,
        message_history: list[dict[str, Any] | Message] = ...,
        response_format: dict[str, Any] | type[BaseModel] | None = ...,
        logging_path: Path | None = ...,
        **kwargs: Any,
    ) -> LLMResponse: ...
