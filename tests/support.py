"""Builders and the stub endpoint shared by the tests."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from assistant_bench.catalog import Price, System


FIXTURES = Path(__file__).parent / 'fixtures'
EXPORT_FILE = FIXTURES / 'nixos-config.json'


def make_system(**overrides: Any) -> System:
    fields: dict[str, Any] = {
        'name': 'acme-large',
        'route': 'acme/large',
        'effort': 'medium',
        'prompt': None,
        'price': Price(input=2.0, output=10.0, cache_read=0.2),
    }
    return System(**(fields | overrides))


# ── Stub Endpoint ──


def completion(
    content: str | None = 'Hello there.',
    finish_reason: str = 'stop',
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        'id': 'chatcmpl-1',
        'object': 'chat.completion',
        'created': 0,
        'model': 'large-2026',
        'choices': [
            {
                'index': 0,
                'finish_reason': finish_reason,
                'message': {'role': 'assistant', 'content': content},
            }
        ],
        'usage': usage
        or {
            'prompt_tokens': 120,
            'completion_tokens': 40,
            'total_tokens': 160,
            'prompt_tokens_details': {'cached_tokens': 100},
            'completion_tokens_details': {'reasoning_tokens': 25},
        },
    }


@dataclass
class Request:
    path: str
    headers: dict[str, str]
    body: dict[str, Any]


@dataclass
class StubEndpoint:
    """A chat-completions server on 127.0.0.1 that records requests."""

    url: str
    key_file: Path
    requests: list[Request] = field(default_factory=list)
    status: int = 200
    response: dict[str, Any] = field(default_factory=completion)

    @property
    def env(self) -> dict[str, str]:
        return {
            'BENCH_API_BASE_URL': f'{self.url}/v1',
            'BENCH_API_KEY_FILE': str(self.key_file),
            'BENCH_API_HEADERS': '{"x-trace": "bench"}',
        }
