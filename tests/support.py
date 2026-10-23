"""Builders and the stub endpoint shared by the tests."""

import asyncio
import ipaddress
import json
import shutil
import socket
import ssl
import textwrap
import threading
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import numpy as np
from harbor.job_plan import JobPlan
from harbor.models.agent.context import AgentContext
from harbor.models.job.config import JobConfig
from harbor.models.job.lock import TaskLock, TrialLock, VerifierLock
from harbor.models.task import config as harbor_task_config
from harbor.models.task.id import LocalTaskId
from harbor.models.trial.config import (
    AgentConfig,
    EnvironmentConfig,
    TaskConfig,
    TrialConfig,
)
from harbor.models.trial.result import AgentInfo, ExceptionInfo, TrialResult
from harbor.models.verifier.result import VerifierResult
from harbor.trial.regrade import local_task_name

from assistant_bench.arena.service import Arena, load_families
from assistant_bench.arena.store import (
    DEFECTS,
    SWAPPED,
    Ballot,
    Choice,
    Defects,
    Side,
    Vote,
    VoteStore,
)
from assistant_bench.catalog import Price, System
from assistant_bench.families import Gate, TaskTestsSettings
from assistant_bench.harnesses.endpoint import BASE_URL_VAR, HEADERS_VAR, KEY_FILE_VAR
from assistant_bench.harnesses.generate import OUTPUT_FILE
from assistant_bench.jobs import render_task_config
from assistant_bench.outputs import Output, write_outputs
from assistant_bench.paths import FAMILIES_DIR, data_dir


FIXTURES = Path(__file__).parent / 'fixtures'
EXPORT_FILE = FIXTURES / 'nixos-config.json'
REPORT_FIXTURE = FIXTURES / 'report.json'
EARLY_REPORT_FIXTURE = FIXTURES / 'report-early.json'
REPORT_SCHEMA_FIXTURE = FIXTURES / 'report.schema.json'


# ── Families ──


TASK_SETTINGS = TaskTestsSettings(
    scorer='tests', display_name='Family', samples=1, max_output_tokens=64
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
            max_output_tokens = 64
            categories = ["message"]
            reference = "{systems[0]}"
            """
        )
    )
    cases = directory / 'cases.jsonl'
    cases.write_text('')
    return cases


def write_task_family(families_dir: Path, *task_ids: str, name: str = 'f') -> None:
    """Write a task family in the `smoke` category, copying the echo task per ID."""
    directory = families_dir / name
    (directory / 'tasks').mkdir(parents=True)
    (directory / 'family.toml').write_text(
        textwrap.dedent(
            f"""\
            scorer = "tests"
            display_name = "Family {name}"
            samples = 1
            categories = ["smoke"]
            """
        )
    )
    for task_id in task_ids:
        target = directory / 'tasks' / task_id
        shutil.copytree(FAMILIES_DIR / 'smoke' / 'tasks' / 'echo', target)
        config = target / 'task.toml'
        config.write_text(config.read_text().replace('smoke/echo', f'{name}/{task_id}'))


# ── Systems and Trials ──


def make_system(**overrides: Any) -> System:
    fields: dict[str, Any] = {
        'name': 'acme-large',
        'display_name': 'Acme Large',
        'route': 'acme/large',
        'effort': 'medium',
        'prompt': None,
        'price': Price(input=2.0, output=10.0, cache_read=0.2),
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
) -> None:
    """Write a trial's result and lock, its job's files, and its task's instruction.

    An `instruction` of `None` leaves a task that `render_tasks` wrote untouched.
    The trial records its installed task's version unless one is given.
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
        task=TaskLock(
            name=Path(task).name,
            version=version,
            type='local',
            digest=f'sha256:{"0" * 64}',
        ),
        agent=agent,
        environment=EnvironmentConfig(),
        verifier=VerifierLock(),
    )
    (trial_dir / 'result.json').write_text(result.model_dump_json())
    (trial_dir / 'lock.json').write_text(lock.model_dump_json())
    (job_dir / 'config.json').touch()
    (job_dir / 'result.json').touch()


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


@dataclass
class Request:
    path: str
    headers: dict[str, str]
    body: dict[str, Any]


@dataclass
class StubEndpoint:
    """A chat-completions server on a loopback address that records requests."""

    url: str
    key_file: Path
    ca_file: Path | None = None
    requests: list[Request] = field(default_factory=list)
    status: int = 200
    response: dict[str, Any] = field(default_factory=completion)

    @property
    def env(self) -> dict[str, str]:
        return {
            BASE_URL_VAR: f'{self.url}/v1',
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
            payload = json.dumps(stub.response).encode()
            self.send_response(stub.status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('x-request-id', 'req-1')
            self.end_headers()
            self.wfile.write(payload)

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
    yield stub
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


def make_ballot(a: Output, b: Output, **fields: Any) -> Ballot:
    """Return a vote for `a`, shown on the left, against `b` on `a`'s case."""
    return Ballot.model_validate(_ballot_fields(a, b) | fields)


def make_vote(a: Output, b: Output, **fields: Any) -> Vote:
    """Return vote 1, recorded at `AT`, for `a` on the left against `b`."""
    return Vote.model_validate(
        {'id': 1, 'recorded_at': AT} | _ballot_fields(a, b) | fields
    )


def _ballot_fields(a: Output, b: Output) -> dict[str, Any]:
    return {
        'session': 's',
        'case': a.case,
        'a': Side.of(a),
        'b': Side.of(b),
        'choice': 'a',
        'duration_sec': 5.0,
    }


def write_votes_family(
    families_dir: Path,
    name: str,
    systems: list[str],
    categories: dict[str, str],
    render: str = 'text',
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
            max_output_tokens = 64
            categories = {json.dumps(sorted(set(categories.values())))}
            reference = "{systems[0]}"
            render = "{render}"
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
    (directory / 'cases.jsonl').write_text(
        ''.join(
            json.dumps(
                {
                    'id': case,
                    'category': category,
                    'source': 'synthetic',
                    'instruction': f'Write about {case}.',
                    'checklist': ['Stays on topic'],
                }
            )
            + '\n'
            for case, category in categories.items()
        )
    )


ARENA_SYSTEMS = ['large', 'small']
ARENA_CASES = {'c1': 'reply', 'c2': 'docs', 'c3': 'reply'}
RENDER_ORIGIN = 'http://127.0.0.1:9'


class Clock:
    def __init__(self) -> None:
        self.now = AT

    def __call__(self) -> datetime:
        return self.now


@dataclass
class Refits:
    """The comparisons behind each refit of the scheduler's board, and its root."""

    comparisons: list[int] = field(default_factory=list)
    roots: list[str] = field(default_factory=list)


def make_arena(
    families_dir: Path,
    clock: Clock,
    store: VoteStore,
    texts: dict[str, str] | None = None,
    cut_off: tuple[str, ...] = (),
    render: str = 'text',
) -> Arena:
    """Serve family `f`, where `texts` overrides each system's output text."""
    write_votes_family(families_dir, 'f', ARENA_SYSTEMS, ARENA_CASES, render=render)
    outputs = passing_outputs(ARENA_CASES, ARENA_SYSTEMS).values()
    truncated = {'finish_reason': 'length', 'gate_failures': [Gate.TRUNCATED]}
    write_outputs(
        'f',
        [
            output.model_copy(
                update={'text': (texts or {}).get(output.system, output.text)}
                | (truncated if output.system in cut_off else {})
            )
            for output in outputs
        ],
    )
    return Arena(store, load_families(), RENDER_ORIGIN, clock, np.random.default_rng(0))


SYNTHETIC_SYSTEMS = {
    'acme-large': 0.6,
    'acme-small': 0.2,
    'acme-pro': 0.5,
    'acme-flash': -0.1,
    'acme-mini': -0.4,
}
SYNTHETIC_STRAYS = {'acme-edge': 0.3, 'acme-nano': -0.2, 'acme-tiny': -1.0}
SYNTHETIC_START = datetime(2026, 10, 1, 9, tzinfo=UTC)
SYNTHETIC_CLOSE = datetime(2026, 10, 21, 18, tzinfo=UTC)
SYNTHETIC_EARLY_CLOSE = datetime(2026, 10, 1, 18, tzinfo=UTC)


def write_synthetic_round(
    families_dir: Path, store: VoteStore, sessions: int = 10
) -> None:
    """Write family `arena` with outputs and about 30 votes per daily session.

    A seeded rater follows Bradley-Terry with the strengths above and a small
    left-side bias, ties 15% and calls both bad 5% of the time, and swaps sides
    on about one vote in ten from an earlier session. About 3% of the outputs
    fail a gate, so their pairs are settled without a vote.

    Each session also pits `acme-edge` against `acme-nano` on the two cases
    where every other output passes, which leaves them unranked, and casts two
    votes for `acme-tiny` on `case-retired`, which has outputs but is no longer
    a case. Those votes are dropped, so `acme-tiny` ranks only through settled
    results, where it fails a gate on most cases.
    """
    rng = np.random.default_rng(7)
    systems = list(SYNTHETIC_SYSTEMS)
    categories = ['docs', 'message', 'reply']
    cases = {f'case-{i:02}': categories[i % 3] for i in range(30)}
    everyone = [*systems, *SYNTHETIC_STRAYS]
    write_votes_family(families_dir, 'arena', everyone, cases)

    def output(case: str, system: str, sample: int, failing: float) -> Output:
        words = int(rng.integers(40, 400))
        return make_output(
            trial=f'{case}__{system}__{sample}',
            job='job-1',
            case=case,
            system=system,
            fingerprint=f'fp-{system}',
            text=' '.join([system if system in SYNTHETIC_STRAYS else 'word'] * words),
            gate_failures=[Gate.TRUNCATED] if rng.random() < failing else [],
            output_tokens=words * 2,
            cost_usd=words * 2e-5 * (1 + everyone.index(system)),
            latency_sec=float(rng.uniform(2, 40)),
        )

    outputs = [
        output(case, system, sample, 0.03)
        for case in cases
        for system in systems
        for sample in range(2)
    ]
    failing = {o.case for o in outputs if o.gate_failures}
    clean = [case for case in cases if case not in failing][:2]
    outputs += [
        output(case, system, sample, 0.0)
        for case in clean
        for system in ('acme-edge', 'acme-nano')
        for sample in range(2)
    ]
    failable = set(cases) - set(clean)
    outputs += [
        output(case, 'acme-tiny', sample, 0.7 if case in failable else 0.0)
        for case in [*cases, 'case-retired']
        for sample in range(2)
    ]
    outputs += [output('case-retired', 'acme-large', 0, 0.0)]
    write_outputs('arena', outputs)

    by_slot: dict[tuple[str, str], list[Output]] = {}
    for o in outputs:
        if not o.gate_failures:
            by_slot.setdefault((o.case, o.system), []).append(o)
    strays = [
        (case, pair)
        for case in clean
        for pair in (('acme-edge', 'acme-nano'), ('acme-nano', 'acme-edge'))
    ] + [
        ('case-retired', pair)
        for pair in (('acme-tiny', 'acme-large'), ('acme-large', 'acme-tiny'))
    ]
    recorded: list[tuple[int, Vote]] = []
    for day in range(sessions):
        session = store.start_session('arena', SYNTHETIC_START + timedelta(days=day))
        at = session.started_at
        for case in rng.permutation(list(cases)):
            at += timedelta(seconds=float(rng.uniform(20, 90)))
            earlier = [
                vote
                for vote_day, vote in recorded
                if vote_day < day and vote.repeat_of is None
            ]
            if earlier and rng.random() < 0.1:
                original = earlier[rng.integers(len(earlier))]
                agree = rng.random() < 0.85
                ballot = Ballot(
                    session=session.id,
                    case=original.case,
                    a=original.b,
                    b=original.a,
                    choice=SWAPPED[original.choice] if agree else original.choice,
                    duration_sec=float(rng.uniform(15, 120)),
                    repeat_of=original.id,
                )
            else:
                left, right = rng.choice(systems, size=2, replace=False)
                lefts = by_slot.get((str(case), str(left)), [])
                rights = by_slot.get((str(case), str(right)), [])
                if not (lefts and rights):
                    continue
                a = lefts[rng.integers(len(lefts))]
                b = rights[rng.integers(len(rights))]
                ballot = _simulated_vote(rng, session.id, a, b)
            recorded.append((day, store.record(ballot, at)))
        for case, (left, right) in strays:
            at += timedelta(seconds=float(rng.uniform(20, 90)))
            a, b = by_slot[case, left][0], by_slot[case, right][-1]
            store.record(_simulated_vote(rng, session.id, a, b), at)


def _simulated_vote(
    rng: np.random.Generator, session: str, a: Output, b: Output
) -> Ballot:
    strengths = SYNTHETIC_SYSTEMS | SYNTHETIC_STRAYS
    gap = strengths[a.system] - strengths[b.system] + 0.1
    draw = rng.random()
    choice: Choice
    if draw < 0.15:
        choice = 'tie'
    elif draw < 0.2:
        choice = 'both_bad'
    else:
        choice = 'a' if rng.random() < 1 / (1 + np.exp(-gap)) else 'b'
    tagged = (
        frozenset({DEFECTS[rng.integers(len(DEFECTS))]})
        if rng.random() < 0.2
        else frozenset()
    )
    defects = Defects(b=tagged) if choice == 'a' else Defects(a=tagged)
    return Ballot(
        session=session,
        case=a.case,
        a=Side.of(a),
        b=Side.of(b),
        choice=choice,
        defects=defects,
        duration_sec=float(rng.uniform(15, 120)),
    )
