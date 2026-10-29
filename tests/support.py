"""Builders and the stub endpoint shared by the tests."""

import asyncio
import ipaddress
import json
import math
import shutil
import socket
import sqlite3
import ssl
import textwrap
import threading
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from random import Random
from typing import Any, Literal
from uuid import UUID

import tomli_w
from harbor.job_plan import JobPlan
from harbor.models.agent.context import AgentContext
from harbor.models.job.config import JobConfig
from harbor.models.job.lock import TaskLock, TrialLock, VerifierLock
from harbor.models.task import config as harbor_task_config
from harbor.models.task.id import LocalTaskId
from harbor.models.trial.config import (
    AgentConfig,
    EnvironmentConfig,
    SourceTrialConfig,
    TaskConfig,
    TrialConfig,
)
from harbor.models.trial.result import (
    AgentInfo,
    ExceptionInfo,
    TimingInfo,
    TrialResult,
)
from harbor.models.verifier.result import VerifierResult
from harbor.trial.regrade import local_task_name

from assistant_bench.arena.d1 import Param, Result, Row
from assistant_bench.arena.publication import (
    CaseFacts,
    Inventory,
    OutputFacts,
    Publication,
    output_key,
)
from assistant_bench.arena.votes import Cut, Snapshot, TaggedDefects, Vote
from assistant_bench.catalog import Price, System
from assistant_bench.families import (
    Case,
    CaseSource,
    Category,
    Choice,
    Defect,
    Family,
    Gate,
    TaskTestsSettings,
    VotesSettings,
)
from assistant_bench.generation import Generation
from assistant_bench.harnesses.endpoint import (
    ANTHROPIC_BASE_URL_VAR,
    BASE_URL_VAR,
    HEADERS_VAR,
    KEY_FILE_VAR,
)
from assistant_bench.harnesses.generate import OUTPUT_FILE
from assistant_bench.jobs import render_task_config
from assistant_bench.outputs import Output
from assistant_bench.paths import FAMILIES_DIR, data_dir


FIXTURES = Path(__file__).parent / 'fixtures'
EXPORT_FILE = FIXTURES / 'nixos-config.json'
REPORT_FIXTURE = FIXTURES / 'report.json'
EARLY_REPORT_FIXTURE = FIXTURES / 'report-early.json'
TESTS_REPORT_FIXTURE = FIXTURES / 'report-tests.json'
REPORT_SCHEMA_FIXTURE = FIXTURES / 'report.schema.json'


# ── Families ──


PROSE_DEFECTS: tuple[Defect, ...] = (
    'scope',
    'facts',
    'clarity',
    'tone',
    'mechanics',
    'formulaic',
    'length',
)
CODE_DEFECTS: tuple[Defect, ...] = (
    'scope',
    'conventions',
    'complexity',
    'testing',
    'clarity',
)

TASK_SETTINGS = TaskTestsSettings(
    scorer='tests',
    display_name='Family',
    samples=1,
    harness='terminus-2',
)


def write_case_family(families_dir: Path, systems: list[str], name: str = 'f') -> Path:
    """Write a case family in the `message` category, returning its empty cases file."""
    directory = families_dir / name
    directory.mkdir(parents=True)
    (directory / 'family.toml').write_text(
        textwrap.dedent(
            f"""\
            scorer = "votes"
            display_name = "Family {name}"
            systems = {json.dumps(systems)}
            samples = 1
            categories = ["message"]
            reference = "{systems[0]}"
            """
        )
    )
    cases = directory / 'cases.toml'
    cases.write_text('cases = []\n')
    return cases


def write_task_family(
    families_dir: Path,
    *task_ids: str,
    name: str = 'f',
    systems: tuple[str, ...] = (),
    author_vendor: str = 'acme',
    defects: tuple[Defect, ...] = CODE_DEFECTS,
    samples: int = 1,
    categories: Mapping[str, str] | None = None,
) -> None:
    """Write a task family, copying the echo task per ID.

    A family that lists systems is scored by votes, tagged with `defects`, and
    runs them as terminus-2, and one without is scored by tests. `categories`
    maps every task ID to its category, and the family allows only `smoke`
    without it. A `cases.toml` already written for the same name is removed,
    since a family has one source.
    """
    if categories is None:
        categories = dict.fromkeys(task_ids, 'smoke')
    allowed = sorted(set(categories.values())) or ['smoke']
    directory = families_dir / name
    (directory / 'cases.toml').unlink(missing_ok=True)
    (directory / 'tasks').mkdir(parents=True)
    scoring = (
        f"""\
        scorer = "votes"
        systems = {json.dumps(list(systems))}
        reference = "{systems[0]}"
        render = "diff"
        defects = {json.dumps(list(defects))}
        harness = "terminus-2"
        """
        if systems
        else 'scorer = "tests"\n'
    )
    (directory / 'family.toml').write_text(
        textwrap.dedent(scoring)
        + textwrap.dedent(
            f"""\
            display_name = "Family {name}"
            samples = {samples}
            categories = {json.dumps(allowed)}
            """
        )
    )
    for task_id in task_ids:
        target = directory / 'tasks' / task_id
        shutil.copytree(FAMILIES_DIR / 'smoke' / 'tasks' / 'echo', target)
        config = target / 'task.toml'
        config.write_text(
            config.read_text()
            .replace('smoke/echo', f'{name}/{task_id}')
            .replace('category = "smoke"', f'category = "{categories[task_id]}"')
        )
        if systems:
            config.write_text(
                config.read_text().replace(
                    '[metadata]',
                    f'[metadata]\nauthor = "{systems[0]}"\n'
                    f'author_vendor = "{author_vendor}"',
                )
            )


# ── Systems and Trials ──


def make_generation(protocol: int = 2, harbor: str = '0.24.0') -> Generation:
    return Generation(
        protocol=protocol,
        harbor=harbor,
        messages_protocol=1,
        anthropic_api_version='2023-06-01',
    )


GENERATION = make_generation()


def make_system(**overrides: Any) -> System:
    fields: dict[str, Any] = {
        'name': 'acme-large',
        'display_name': 'Acme Large',
        'vendor': 'acme',
        'route': 'acme/large',
        'api': 'openai-chat-completions',
        'harness': 'generate',
        'generation': GENERATION,
        'max_output_tokens': 64,
        'context_window': 8192,
        'effort': 'medium',
        'prompt': None,
        'price': Price(input=2.0, output=10.0, cache_read=0.2, cache_write=2.5),
    }
    return System(**(fields | overrides))


def make_output(**fields: Any) -> Output:
    empty = dict.fromkeys(Output.model_fields)
    return Output.model_validate(
        empty | {'trial': 't', 'job': 'j', 'case': 'c', 'system': 's'} | fields
    )


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
    instruction: str | None = 'Hi.',
    version: str | None = None,
    workspace: dict[str, str] | None = None,
    source: UUID | None = None,
    agent_execution: TimingInfo | None = None,
    environment: EnvironmentConfig | None = None,
    verifier_disabled: bool = False,
) -> UUID:
    """Write a trial's result and lock, its job's files, and its task's instruction.

    An `instruction` of `None` leaves a task that `render_tasks` wrote untouched.
    The trial records its installed task's version unless one is given. Each
    `workspace` file lands in the trial's `/app` artifact, and `source` names the
    trial a regrade derives from. Returns the trial's ID.
    """
    if instruction is not None:
        task_dir = data_dir() / 'tasks' / task
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / 'instruction.md').write_text(instruction)
        (task_dir / 'task.toml').write_text(
            render_task_config(task, instruction).model_dump_toml()
        )
    if version is None:
        version = _installed_version(task)
    trial_dir = job_dir / name
    (trial_dir / 'agent').mkdir(parents=True)
    if output is not None:
        (trial_dir / 'agent' / OUTPUT_FILE).write_text(output)
    for path, content in (workspace or {}).items():
        file = trial_dir / 'artifacts' / 'app' / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content)
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
        config=TrialConfig(
            task=TaskConfig(path=Path('/tasks') / task),
            agent=agent,
            environment=environment or EnvironmentConfig(),
            source_trial=None
            if source is None
            else SourceTrialConfig(
                action='regrade', type='local', trial_id=source, path=Path('/moved')
            ),
        ),
        agent_info=AgentInfo(name=agent.name or 'generate', version='1'),
        agent_result=context,
        verifier_result=None
        if reward is None
        else VerifierResult(rewards={'reward': reward}),
        exception_info=exception if error else None,
        agent_execution=agent_execution,
    )
    lock = TrialLock(
        task=TaskLock(
            name=Path(task).name,
            version=version,
            type='local',
            digest=f'sha256:{"0" * 64}',
        ),
        agent=agent,
        environment=environment or EnvironmentConfig(),
        verifier=VerifierLock(disable=verifier_disabled),
    )
    (trial_dir / 'result.json').write_text(result.model_dump_json())
    (trial_dir / 'lock.json').write_text(lock.model_dump_json())
    (job_dir / 'config.json').touch()
    (job_dir / 'result.json').touch()
    return result.id


def _installed_version(task: str) -> str:
    config = harbor_task_config.TaskConfig.model_validate_toml(
        (data_dir() / 'tasks' / task / 'task.toml').read_text()
    )
    assert config.task is not None
    assert config.task.version is not None
    return config.task.version


def finish_job(job: JobConfig) -> Path:
    """Write a successful trial for each trial Harbor plans, and return the job's path.

    Each lock comes from Harbor's own plan, so a later job's diff can reuse the trial.
    A job with the verifier enabled records reward 1 for each trial.
    """
    plan = asyncio.run(JobPlan.from_config(job))
    job_dir = job.jobs_dir / job.job_name
    for trial, lock in zip(plan.trial_configs, plan.trial_locks, strict=True):
        assert trial.task.path is not None
        trial_dir = job_dir / trial.trial_name
        trial_dir.mkdir(parents=True)
        result = TrialResult(
            task_name=local_task_name(trial.task.path),
            trial_name=trial.trial_name,
            trial_uri=f'file://{trial_dir}',
            task_id=LocalTaskId(path=trial.task.path),
            task_checksum='0',
            config=trial,
            agent_info=AgentInfo(name='generate', version='1'),
            verifier_result=None
            if job.verifier.disable
            else VerifierResult(rewards={'reward': 1.0}),
            finished_at=datetime(2026, 10, 21, tzinfo=UTC),
        )
        (trial_dir / 'result.json').write_text(result.model_dump_json())
        (trial_dir / 'lock.json').write_text(lock.model_dump_json())
    (job_dir / 'config.json').write_text(job.model_dump_json())
    (job_dir / 'result.json').write_text('{}')
    return job_dir


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


def response(
    content: str | None = 'Hello there.',
    *,
    response_id: str = 'resp-1',
    status: str = 'completed',
    incomplete_reason: str | None = None,
    refusal: str | None = None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parts: list[dict[str, Any]] = []
    if content is not None:
        parts.append({'type': 'output_text', 'text': content, 'annotations': []})
    if refusal is not None:
        parts.append({'type': 'refusal', 'refusal': refusal})
    output = (
        [
            {
                'id': 'msg-1',
                'type': 'message',
                'role': 'assistant',
                'status': 'completed' if status == 'completed' else 'incomplete',
                'content': parts,
            }
        ]
        if parts
        else []
    )
    return {
        'id': response_id,
        'object': 'response',
        'created_at': 0,
        'model': 'large-2026',
        'status': status,
        'error': None,
        'incomplete_details': {'reason': incomplete_reason}
        if incomplete_reason is not None
        else None,
        'output': output,
        'parallel_tool_calls': False,
        'tool_choice': 'auto',
        'tools': [],
        'usage': usage
        or {
            'input_tokens': 120,
            'input_tokens_details': {'cached_tokens': 100, 'cache_write_tokens': 0},
            'output_tokens': 40,
            'output_tokens_details': {'reasoning_tokens': 25},
            'total_tokens': 160,
        },
    }


def reasoned_response(
    content: str | None,
    response_id: str,
    phase: Literal['commentary', 'final_answer'] = 'commentary',
) -> dict[str, Any]:
    payload = response(content, response_id=response_id)
    if payload['output']:
        payload['output'][0]['phase'] = phase
    payload['output'].insert(
        0,
        {
            'id': f'rs-{response_id}',
            'type': 'reasoning',
            'summary': [],
            'encrypted_content': f'opaque-{response_id}',
        },
    )
    return payload


def terminus_completion(
    keystrokes: str = '', *, task_complete: bool = True
) -> dict[str, Any]:
    """Return a completion carrying terminus-2's JSON reply.

    The terminal sends `keystrokes` as one command, so it needs its own newline.
    """
    commands = [{'keystrokes': keystrokes, 'duration': 0.1}] if keystrokes else []
    reply = {
        'analysis': '',
        'plan': '',
        'commands': commands,
        'task_complete': task_complete,
    }
    return completion(json.dumps(reply))


def message(
    text: str = 'Hello there.',
    *,
    message_id: str = 'msg-1',
    stop_reason: str = 'end_turn',
    stop_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        'id': message_id,
        'type': 'message',
        'role': 'assistant',
        'model': 'large-2026',
        'content': [
            {
                'type': 'thinking',
                'thinking': 'Private reasoning.',
                'signature': f'signed-{message_id}',
            },
            {'type': 'text', 'text': text},
        ],
        'stop_reason': stop_reason,
        'stop_sequence': None,
        'stop_details': stop_details,
        'usage': {
            'input_tokens': 20,
            'cache_read_input_tokens': 100,
            'cache_creation_input_tokens': 10,
            'output_tokens': 40,
        },
    }


def message_stream(payload: dict[str, Any]) -> list[bytes]:
    """Encode native events, including thinking and signature deltas, for the SDK."""
    start: dict[str, Any] = payload | {
        'content': [],
        'stop_reason': None,
        'stop_details': None,
    }
    if payload.get('usage') is not None:
        start['usage'] = payload['usage'] | {'output_tokens': 0}
    events: list[dict[str, Any]] = [{'type': 'message_start', 'message': start}]
    for index, block in enumerate(payload['content']):
        kind = block['type']
        initial = (
            {'type': 'text', 'text': ''}
            if kind == 'text'
            else {'type': 'thinking', 'thinking': '', 'signature': ''}
            if kind == 'thinking'
            else block
        )
        events.append(
            {'type': 'content_block_start', 'index': index, 'content_block': initial}
        )
        deltas = (
            [{'type': 'text_delta', 'text': block['text']}]
            if kind == 'text'
            else [
                {'type': 'thinking_delta', 'thinking': block['thinking']},
                {'type': 'signature_delta', 'signature': block['signature']},
            ]
            if kind == 'thinking'
            else []
        )
        events.extend(
            {'type': 'content_block_delta', 'index': index, 'delta': delta}
            for delta in deltas
        )
        events.append({'type': 'content_block_stop', 'index': index})
    events.extend(
        [
            {
                'type': 'message_delta',
                'delta': {
                    'stop_reason': payload['stop_reason'],
                    'stop_sequence': payload['stop_sequence'],
                    'stop_details': payload['stop_details'],
                },
                'usage': payload.get('usage') or {},
            },
            {'type': 'message_stop'},
        ]
    )
    return [
        f'event: {event["type"]}\ndata: {json.dumps(event)}\n\n'.encode()
        for event in events
    ]


@dataclass
class Request:
    path: str
    headers: dict[str, str]
    body: dict[str, Any]


@dataclass
class StubEndpoint:
    """An OpenAI-compatible server on a loopback address that records requests."""

    url: str
    key_file: Path
    ca_file: Path | None = None
    requests: list[Request] = field(default_factory=list)
    status: int = 200
    statuses: list[int] = field(default_factory=list)
    response_headers: dict[str, str] = field(default_factory=dict)
    response: dict[str, Any] = field(default_factory=completion)
    responses: list[dict[str, Any]] = field(default_factory=list)
    stream: list[bytes] | None = None
    streams: list[list[bytes]] = field(default_factory=list)
    stream_gate: threading.Event | None = None
    stream_gate_after: int = 0
    stream_waiting: threading.Event = field(default_factory=threading.Event)
    request_received: threading.Event = field(default_factory=threading.Event)
    response_gate: threading.Event | None = None
    response_gate_after: int = 0
    response_waiting: threading.Event = field(default_factory=threading.Event)

    @property
    def env(self) -> dict[str, str]:
        return {
            BASE_URL_VAR: f'{self.url}/v1',
            ANTHROPIC_BASE_URL_VAR: f'{self.url}/anthropic',
            KEY_FILE_VAR: str(self.key_file),
            HEADERS_VAR: '{"x-trace": "bench"}',
        }


class IPv6Server(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def serve_stub(
    tmp_path: Path,
    context: ssl.SSLContext | None = None,
    ca_file: Path | None = None,
    host: str = '127.0.0.1',
) -> Iterator[StubEndpoint]:
    """Serve the stub endpoint on `host` until the generator resumes."""
    key_file = tmp_path / 'api-key'
    key_file.write_text('test-key\n')
    stub: StubEndpoint

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers['Content-Length'])
            stub.requests.append(
                Request(
                    path=self.path,
                    headers={k.lower(): v for k, v in self.headers.items()},
                    body=json.loads(self.rfile.read(length)),
                )
            )
            stub.request_received.set()
            if (
                stub.response_gate is not None
                and len(stub.requests) > stub.response_gate_after
            ):
                stub.response_waiting.set()
                stub.response_gate.wait()
            reply = stub.responses.pop(0) if stub.responses else stub.response
            stream = stub.streams.pop(0) if stub.streams else stub.stream
            payload = (
                b''.join(stream) if stream is not None else json.dumps(reply).encode()
            )
            self.send_response(stub.statuses.pop(0) if stub.statuses else stub.status)
            self.send_header(
                'Content-Type',
                'text/event-stream' if stream is not None else 'application/json',
            )
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('x-request-id', 'req-1')
            self.send_header('request-id', 'req-1')
            for name, value in stub.response_headers.items():
                self.send_header(name, value)
            with suppress(BrokenPipeError, ConnectionResetError):
                self.end_headers()
                for index, chunk in enumerate(stream or [payload]):
                    if stub.stream_gate is not None and index == stub.stream_gate_after:
                        stub.stream_waiting.set()
                        stub.stream_gate.wait()
                    self.wfile.write(chunk)
                    self.wfile.flush()

        def log_message(self, format: str, *args: Any) -> None:
            pass

    ipv6 = ipaddress.ip_address(host).version == 6
    server = (IPv6Server if ipv6 else ThreadingHTTPServer)((host, 0), Handler)
    scheme = 'http'
    if context is not None:
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = 'https'
    stub = StubEndpoint(
        url=f'{scheme}://{f"[{host}]" if ipv6 else host}:{server.server_port}',
        key_file=key_file,
        ca_file=ca_file,
    )
    thread = threading.Thread(
        target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True
    )
    thread.start()
    try:
        yield stub
    finally:
        if stub.response_gate is not None:
            stub.response_gate.set()
        if stub.stream_gate is not None:
            stub.stream_gate.set()
        server.shutdown()
        thread.join()
        server.server_close()


# ── Arena ──


AT = datetime(2026, 10, 21, 9, tzinfo=UTC)


def passing_outputs(
    cases: Iterable[str],
    systems: Iterable[str],
    text: str = '{system} answers {case}',
    **fields: Any,
) -> dict[tuple[str, str], Output]:
    """Return one output per case and system that passes every gate.

    Each text formats `text` with the case and the system.
    """
    listed = list(systems)
    return {
        (case, system): make_output(
            **{
                'trial': f'{case}__{system}',
                'job': 'job-1',
                'case': case,
                'system': system,
                'fingerprint': f'fp-{system}',
                'text': text.format(case=case, system=system),
                'finish_reason': 'stop',
                'gate_failures': [],
            }
            | fields
        )
        for case in cases
        for system in listed
    }


def write_votes_family(
    families_dir: Path,
    name: str,
    systems: list[str],
    categories: dict[str, str],
    render: str = 'text',
    *,
    public_voting: bool = False,
) -> None:
    """Write a family scored by votes, with one synthetic case per category entry.

    Each case's task holds its instruction, as `bench run` would have rendered it.
    """
    directory = families_dir / name
    directory.mkdir(parents=True)
    (directory / 'family.toml').write_text(
        textwrap.dedent(
            f"""\
            scorer = "votes"
            aspect = "writing"
            display_name = "Family {name}"
            systems = {json.dumps(systems)}
            samples = 2
            categories = {json.dumps(sorted(set(categories.values())))}
            reference = "{systems[0]}"
            render = "{render}"
            public_voting = {json.dumps(public_voting)}
            defects = {json.dumps(list(PROSE_DEFECTS))}
            """
        )
    )
    for case in categories:
        instruction = f'Write about {case}.'
        task = data_dir() / 'tasks' / name / case
        task.mkdir(parents=True, exist_ok=True)
        (task / 'instruction.md').write_text(instruction)
        (task / 'task.toml').write_text(
            render_task_config(f'{name}/{case}', instruction).model_dump_toml()
        )
    (directory / 'cases.toml').write_text(
        tomli_w.dumps(
            {
                'cases': [
                    {
                        'id': case,
                        'category': category,
                        'source': 'synthetic',
                        'instruction': f'Write about {case}.',
                        'checklist': ['Stays on topic'],
                    }
                    for case, category in categories.items()
                ]
            }
        )
    )


ARENA_SYSTEMS = ('acme-large', 'acme-small', 'acme-pro')
ARENA_NAMES = {system: system.replace('-', ' ').title() for system in ARENA_SYSTEMS}


def make_votes_family(
    name: str = 'writing-fixture', *, public_voting: bool = True, diff: bool = False
) -> Family:
    """Return a votes family of `ARENA_SYSTEMS`, rendering task diffs if `diff`."""
    return Family(
        name=name,
        source='tasks' if diff else 'cases',
        settings=VotesSettings(
            scorer='votes',
            aspect='coding' if diff else 'writing',
            display_name='Fixture',
            systems=list(ARENA_SYSTEMS),
            samples=1,
            reference=ARENA_SYSTEMS[0],
            public_voting=public_voting,
            render='diff' if diff else 'text',
            categories=['bug-fix'] if diff else ['docs', 'reply'],
            defects=list(CODE_DEFECTS if diff else PROSE_DEFECTS),
        ),
    )


def make_case(
    case: str, source: CaseSource = 'synthetic', *, category: Category = 'docs'
) -> Case:
    return Case(
        id=case,
        category=category,
        source=source,
        instruction=f'Write about {case}.',
        checklist=[f'Mentions {case}'],
    )


def make_trial(
    case: str,
    system: str,
    gates: list[Gate] | None = None,
    **fields: Any,
) -> Output:
    """Return the output of `system` on `case`, passing unless `gates` failed."""
    return make_output(
        **{
            'case': case,
            'system': system,
            'job': 'job-1',
            'trial': f'{case}__{system}',
            'fingerprint': f'fp-{system}',
            'text': f'{system} on {case}',
            'gate_failures': [] if gates is None else gates,
        }
        | fields
    )


# ── Vote Stores ──


MIGRATIONS_DIR = Path(__file__).parents[1] / 'web' / 'migrations'


class SQLiteDatabase:
    """The `d1.Database` protocol on CPython's sqlite3, in auto-commit like D1."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def execute(self, sql: str, params: Sequence[Param] = ()) -> Result:
        cursor = self.connection.execute(sql, list(params))
        columns = [column[0] for column in cursor.description or ()]
        rows: list[Row] = [dict(zip(columns, row, strict=True)) for row in cursor]
        return Result(rows=rows, changes=max(cursor.rowcount, 0))


def record_vote(
    database: SQLiteDatabase,
    published: Publication,
    now: datetime,
    *,
    actor: str,
    age: timedelta,
    case: int = 0,
    choice: Choice = 'a',
    elapsed_ms: int = 4_000,
    ip_tag: str | None = None,
    exclusion: str | None = None,
) -> int:
    """Record `actor`'s vote on the case's first two outputs from `ip_tag`.

    The vote is `age` old at `now`, its tag defaults to `tag-<actor>`, and its ID
    is returned.
    """
    a, b = [
        row for row in published.outputs if row.case_key == published.cases[case].key
    ][:2]
    tag = ip_tag or f'tag-{actor}'
    database.execute(
        'INSERT OR IGNORE INTO actors (id, created_at, ip_tag) VALUES (?, 0, ?)',
        [actor, tag],
    )
    database.execute(
        'INSERT INTO votes (pair_id, actor_id, family, publication_id, case_key, '
        'a_output, b_output, a_system, b_system, choice, defects, elapsed_ms, '
        'recorded_at, ip_tag, exclusion) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [
            f'pair-{actor}-{case}',
            actor,
            published.family,
            published.id,
            a.case_key,
            a.key,
            b.key,
            a.system,
            b.system,
            choice,
            '{"a": [], "b": ["facts"]}',
            elapsed_ms,
            int((now - age).timestamp() * 1000),
            tag,
            exclusion,
        ],
    )
    (row,) = database.execute('SELECT max(id) AS id FROM votes').rows
    assert isinstance(row['id'], int)
    return row['id']


def arena_database() -> SQLiteDatabase:
    """Return an in-memory store built from the Worker's migration files, in order.

    The store binds at most 100 parameters per statement, as D1 does.
    """
    connection = sqlite3.connect(':memory:', isolation_level=None)
    connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 100)
    connection.execute('PRAGMA foreign_keys = ON')
    for path in sorted(MIGRATIONS_DIR.glob('*.sql')):
        connection.executescript(path.read_text())
    return SQLiteDatabase(connection)


# ── Synthetic Round ──


ROUND_STRENGTHS = {
    'acme-large': 0.6,
    'acme-small': 0.2,
    'acme-pro': 0.5,
    'acme-flash': -0.1,
    'acme-mini': -0.4,
    'acme-edge': 0.3,
    'acme-nano': -0.2,
    'acme-tiny': -1.0,
}
"""Each system's Bradley-Terry strength, where the first five share the voted pool."""

ROUND_NAMES = {system: system.replace('-', ' ').title() for system in ROUND_STRENGTHS}
ROUND_CATEGORIES: tuple[Category, ...] = ('docs', 'message', 'reply')
OWNER_DAYS = 4
ROUND_FAMILY = Family(
    name='arena',
    source='cases',
    settings=VotesSettings(
        scorer='votes',
        display_name='Arena',
        systems=list(ROUND_STRENGTHS),
        samples=2,
        reference='acme-large',
        public_voting=True,
        categories=list(ROUND_CATEGORIES),
        defects=list(PROSE_DEFECTS),
    ),
)


def make_round(days: int, taken_at: datetime) -> tuple[Snapshot, list[Output]]:
    """Simulate family `arena` over `days` days of about 30 votes each.

    Returns the production snapshot and the collected outputs. A seeded rater
    follows Bradley-Terry with the strengths above and a small left bias, ties 15%
    of the time and calls both bad 5%. The owner votes on the first four
    days, past the actor cap, and a new visitor on each later one. About 3%
    of the outputs fail a gate and settle their pairs without a vote, while
    `acme-tiny` fails most cases, so it ranks through settlements alone. Each
    day pits `acme-edge` against `acme-nano` on two cases, which leaves them
    unranked, and drops some votes on retired outputs and two excluded ones.
    Draws use only `Random.random`, whose sequence Python keeps stable for a seed.
    """
    rng = Random(20)

    def below(stop: int) -> int:
        return int(rng.random() * stop)

    def shuffled[T](items: list[T]) -> list[T]:
        return sorted(items, key=lambda _: rng.random())

    everyone = list(ROUND_STRENGTHS)
    voted, strays = everyone[:5], everyone[5:7]
    cases: dict[str, Category] = {
        f'case-{i:02}': ROUND_CATEGORIES[i % 3] for i in range(30)
    }

    outputs: list[Output] = []
    facts: list[OutputFacts] = []
    passing: defaultdict[tuple[str, str], list[OutputFacts]] = defaultdict(list)

    def produce(case: str, system: str, failing: float) -> None:
        length = 40 + below(360)
        failed = bool(rng.random() < failing)
        output = make_output(
            job='job-1',
            trial=f'{case}__{system}__{len(outputs)}',
            case=case,
            system=system,
            fingerprint=f'fp-{system}',
            gate_failures=['empty'] if failed else [],
            cost_usd=length * 2e-5 * (1 + everyone.index(system)),
            latency_sec=2 + 38 * rng.random(),
            output_tokens=length * 2,
        )
        key = output_key('arena', output)
        fact = OutputFacts(
            key=key,
            case_key=case,
            system=system,
            fingerprint=f'fp-{system}',
            verdict='fail' if failed else 'pass',
            errored=False,
            length=length,
            content_digest=key,
        )
        outputs.append(output)
        facts.append(fact)
        if not failed:
            passing[case, system].append(fact)

    for case in cases:
        for system in voted:
            for _ in range(2):
                produce(case, system, 0.03)
    failing = {fact.case_key for fact in facts if fact.verdict == 'fail'}
    clean = [case for case in cases if case not in failing][:2]
    for case in clean:
        for system in strays:
            for _ in range(2):
                produce(case, system, 0.0)
    for case in cases:
        for _ in range(2):
            produce(case, 'acme-tiny', 0.0 if case in clean else 0.7)

    votes: list[Vote] = []

    def vote(actor: str, case: str, left: str, right: str) -> None:
        a, b = (
            passing[case, side][below(len(passing[case, side]))]
            for side in (left, right)
        )
        gap = ROUND_STRENGTHS[left] - ROUND_STRENGTHS[right] + 0.1
        draw = rng.random()
        choice: Choice = (
            'tie'
            if draw < 0.15
            else 'both_bad'
            if draw < 0.2
            else 'a'
            if rng.random() < 1 / (1 + math.exp(-gap))
            else 'b'
        )
        tagged: tuple[Defect, ...] = (
            (PROSE_DEFECTS[below(len(PROSE_DEFECTS))],) if rng.random() < 0.2 else ()
        )
        votes.append(
            Vote(
                id=len(votes) + 1,
                actor=actor,
                case_key=case,
                a_output=a.key,
                b_output=b.key,
                a_system=left,
                b_system=right,
                choice=choice,
                defects=TaggedDefects(
                    a=tagged if choice != 'a' else (),
                    b=tagged if choice == 'a' else (),
                ),
                elapsed_ms=30_000,
            )
        )

    def retired(actor: str, case: str) -> None:
        votes.append(
            Vote(
                id=len(votes) + 1,
                actor=actor,
                case_key=case,
                a_output=f'retired-{len(votes)}-a',
                b_output=f'retired-{len(votes)}-b',
                a_system=voted[0],
                b_system=voted[1],
                choice='a',
                defects=TaggedDefects(),
                elapsed_ms=30_000,
            )
        )

    for day in range(days):
        actor = 'owner' if day < OWNER_DAYS else f'visitor-{day:02}'
        for case in shuffled(list(cases)):
            if day > 0 and rng.random() < 0.1:
                retired(actor, case)
                continue
            left, right = shuffled(voted)[:2]
            if passing[case, left] and passing[case, right]:
                vote(actor, case, left, right)
        for case in clean:
            vote(actor, case, *strays)
            vote(actor, case, *reversed(strays))

    inventory = Inventory(
        id='publication-1',
        family='arena',
        reference='acme-large',
        systems=ROUND_NAMES,
        defects=PROSE_DEFECTS,
        cases=tuple(CaseFacts(key=case, category=c) for case, c in cases.items()),
        outputs=tuple(facts),
    )
    cut = Cut(
        cutoff=len(votes),
        inventory=inventory,
        votes=tuple(votes),
        excluded=2 * days,
    )
    snapshot = Snapshot(family='arena', taken_at=taken_at, production=cut, local=None)
    return snapshot, outputs
