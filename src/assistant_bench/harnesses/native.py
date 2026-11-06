"""The model client and conversation Terminus shares across native SDKs."""

from abc import abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, override

from harbor.llms.base import BaseLLM, LLMResponse, OutputLengthExceededError
from harbor.llms.chat import Chat

from assistant_bench.harnesses.usage import MeasuredUsage


ITEMS_KEY = 'native_items'


class NativeResult(Protocol):
    @property
    def text(self) -> str: ...

    @property
    def items(self) -> list[dict[str, Any]]: ...

    @property
    def details(self) -> dict[str, Any]: ...

    @property
    def usage(self) -> MeasuredUsage | None: ...

    def check(self, *, complete: bool) -> None: ...


class NativeCompletionError(ValueError):
    """An accepted request whose semantic result must not trigger regeneration."""


class NativeLengthExceededError(OutputLengthExceededError):
    """An exhausted completion allowance, carrying its partial result for replay."""

    def __init__(self, message: str, result: NativeResult) -> None:
        super().__init__(message, truncated_response=result.text)
        self.result = result


class NativeChat(Chat):
    """Keep SDK items on the assistant message that owns them."""

    _truncated: NativeResult | None = None

    @override
    async def chat(
        self, prompt: str, logging_path: Path | None = None, **kwargs: Any
    ) -> LLMResponse:
        self._truncated = None
        try:
            response = await super().chat(prompt, logging_path, **kwargs)
        except NativeLengthExceededError as error:
            self._truncated = error.result
            raise
        attach_items(self.messages[-1], response)
        return response

    @override
    def reset_response_chain(self) -> None:
        """Attach partial items after Harbor appends a budget failure outside `chat`."""
        super().reset_response_chain()
        if self._truncated is not None:
            self.messages[-1][ITEMS_KEY] = self._truncated.items
            self._truncated = None


def attach_items(message: dict[str, Any], response: LLMResponse) -> None:
    if response.extra is not None and ITEMS_KEY in response.extra:
        message[ITEMS_KEY] = response.extra[ITEMS_KEY]


@dataclass(frozen=True)
class NativeSettings:
    model_name: str
    context_limit: int
    output_limit: int
    effort: str | None
    logs_dir: Path


class NativeLLM(BaseLLM):
    """Send Harbor's model calls through a native SDK client.

    Harbor keeps tool execution and conversation accounting. `attempts` holds the
    details of every request, and `calls` those of each finished reply.
    """

    def __init__(self, settings: NativeSettings) -> None:
        self.settings = settings
        self.calls: list[dict[str, Any]] = []
        self.attempts: list[dict[str, Any]] = []

    @override
    def get_model_context_limit(self) -> int:
        return self.settings.context_limit

    @override
    def get_model_output_limit(self) -> int:
        return self.settings.output_limit

    @override
    async def call(
        self,
        prompt: str,
        message_history: list[dict[str, Any]] | None = None,
        logging_path: Path | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        attempt: dict[str, Any] = {}
        self.attempts.append(attempt)
        result = await self._request(prompt, message_history or [], attempt, **kwargs)
        details = result.details
        self.calls.append(details)
        result.check(complete=True)
        usage = result.usage
        return LLMResponse(
            content=result.text,
            model_name=details['response_model'],
            response_id=details['response_id'],
            usage=usage.harbor if usage else None,
            extra=details | {ITEMS_KEY: result.items},
        )

    @abstractmethod
    async def _request(
        self,
        prompt: str,
        history: Sequence[Mapping[str, Any]],
        attempt: dict[str, Any],
        **kwargs: Any,
    ) -> NativeResult:
        """Send one request and return its finished result.

        `attempt` must hold the request's details once it returns or raises.
        """
