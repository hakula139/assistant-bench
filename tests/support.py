"""Builders and the stub endpoint shared by the tests."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from harbor.models.agent.context import AgentContext
from harbor.models.job.lock import TaskLock, TrialLock, VerifierLock
from harbor.models.task.id import LocalTaskId
from harbor.models.trial.config import (
    AgentConfig,
    EnvironmentConfig,
    TaskConfig,
    TrialConfig,
)
from harbor.models.trial.result import AgentInfo, ExceptionInfo, TrialResult
from harbor.models.verifier.result import VerifierResult

from assistant_bench.catalog import Price, System
from assistant_bench.jobs import TASK_VERSION


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


def write_trial(
    job_dir: Path,
    name: str,
    agent: AgentConfig,
    *,
    task: str,
    context: AgentContext | None = None,
    reward: float | None = None,
    error: str | None = None,
    output: str | None = None,
    digest: str = f'sha256:{"0" * 64}',
    version: str = TASK_VERSION,
) -> None:
    """Write a trial's result and lock, plus the job files Harbor writes on start."""
    trial_dir = job_dir / name
    (trial_dir / 'agent').mkdir(parents=True)
    if output is not None:
        (trial_dir / 'agent' / 'output.txt').write_text(output)
    exception = ExceptionInfo(
        exception_type=error or '',
        exception_message='',
        exception_traceback='',
        occurred_at=datetime(2026, 10, 21, tzinfo=UTC),
    )
    result = TrialResult(
        task_name=task,
        trial_name=name,
        trial_uri=f'file://{trial_dir}',
        task_id=LocalTaskId(path=Path('/tasks') / task),
        task_checksum='0',
        config=TrialConfig(task=TaskConfig(path=Path('/tasks') / task), agent=agent),
        agent_info=AgentInfo(name=agent.name or 'generate', version='1'),
        agent_result=context,
        verifier_result=None
        if reward is None
        else VerifierResult(rewards={'reward': reward}),
        exception_info=exception if error else None,
    )
    lock = TrialLock(
        task=TaskLock(name=name, version=version, type='local', digest=digest),
        agent=agent,
        environment=EnvironmentConfig(),
        verifier=VerifierLock(),
    )
    (trial_dir / 'result.json').write_text(result.model_dump_json())
    (trial_dir / 'lock.json').write_text(lock.model_dump_json())
    (job_dir / 'config.json').touch()
    (job_dir / 'result.json').touch()


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
    ca_file: Path | None = None
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
