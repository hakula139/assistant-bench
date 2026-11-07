import json
import shutil
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from uuid import UUID

import pytest
import tomli_w
from harbor.models.agent.context import AgentContext
from harbor.models.environment_type import EnvironmentType
from harbor.models.job.config import SourceJobConfig
from harbor.models.job.lock import ExtraDockerComposeLock, TrialLock
from harbor.models.trial import config as trial_config
from harbor.models.trial.result import TimingInfo

from assistant_bench import collection, jobs
from assistant_bench.catalog import Price, System
from assistant_bench.database import SQLiteDatabase
from assistant_bench.diffs import workspace_diff
from assistant_bench.families import (
    Case,
    Family,
    Gate,
    Gates,
    case_task,
    load_cases,
    load_family,
)
from assistant_bench.harnesses import fingerprint
from assistant_bench.harnesses.usage import MeasuredUsage
from assistant_bench.outputs import read_outputs
from assistant_bench.paths import FAMILIES_DIR, data_dir as bench_data_dir
from assistant_bench.runtime import RunContext
from assistant_bench.scoring import checkers
from assistant_bench.scoring.gates import failed_gates
from assistant_bench.spend import Accounting, Entry
from assistant_bench.tasks import (
    TASK_VERSION,
    current_versions,
    render_task_config,
    render_tasks,
)
from support import (
    FIXTURES,
    START,
    VOTES,
    break_setting,
    finish_generation,
    finish_job,
    make_system,
    voted_tasks,
    with_settings,
    write_checkers_family,
    write_trial,
    write_votes_family,
)


GREETING_DIFF = (
    'diff --git a/greeting.txt b/greeting.txt\nnew file mode 100644\n'
    '--- /dev/null\n+++ b/greeting.txt\n@@ -0,0 +1 @@\n+hello\n'
)


def votes_family(**settings: Any) -> Family:
    return VOTES.model_copy(
        update={'settings': VOTES.settings.model_copy(update=settings)}
    )


def current_agent(
    family: Family = VOTES, system: System | None = None
) -> trial_config.AgentConfig:
    resolved = jobs.family_systems(family, [system or make_system()])
    return jobs.job(family, resolved, [], root=bench_data_dir(), sources=[]).agents[0]


def record_trial(
    job_dir: Path,
    name: str,
    agent: trial_config.AgentConfig,
    *,
    family: Family = VOTES,
    **fields: Any,
) -> UUID:
    if family.source == 'cases' and fields.get('instruction', 'Hi.') is not None:
        existing = {
            case.id: case
            for case in load_cases(family, private_root=bench_data_dir() / 'cases')
        }
        case_id = Path(fields['task']).name
        existing[case_id] = Case(
            id=case_id,
            category=family.settings.categories[0]
            if family.settings.categories
            else 'reply',
            source='synthetic',
            instruction=fields.get('instruction', 'Hi.'),
        )
        declare_cases(family, list(existing.values()))

    config = jobs.job(
        family,
        jobs.family_systems(family, [make_system()]),
        [],
        root=bench_data_dir(),
        sources=[],
    )
    return write_trial(
        job_dir,
        name,
        agent,
        environment=config.environment,
        verifier_disabled=config.verifier.disable,
        **fields,
    )


def declare_cases(family: Family, cases: list[Case]) -> None:
    path = bench_data_dir() / 'cases' / f'{family.name}.toml'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps({'cases': [case.model_dump() for case in cases]}))


def render_cases(family: Family, cases: list[Case]) -> list[Path]:
    declare_cases(family, cases)
    return render_tasks(family, cases, root=bench_data_dir())


def record_accounting(
    database: SQLiteDatabase,
    job: str,
    trial: str,
    system: System,
    usage: MeasuredUsage,
    *,
    seconds: int = 0,
) -> None:
    frozen = Accounting.of(system)
    entry = Entry(
        job=job,
        trial=trial,
        system=system.name,
        fingerprint=fingerprint(system),
        started_at=START + timedelta(seconds=seconds),
        finished_at=START + timedelta(seconds=seconds + 1),
        responses=[usage],
        price=frozen.price,
        ceiling_usd=frozen.ceiling_usd,
    )
    database.execute(
        """
        INSERT INTO attempt_entries (
            scope, job, trial, started_at, claim_id, attempt_id, entry_json
        ) VALUES (?, ?, ?, ?, NULL, NULL, ?)
        """,
        ('f', job, trial, entry.started_at.isoformat(), entry.model_dump_json()),
    )


class TestCollect:
    @pytest.fixture(autouse=True)
    def current_sources(
        self,
        families_dir: Path,
        data_dir: Path,
        run_context: RunContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        write_votes_family(families_dir, ['acme-large'], name='smoke-text')
        write_votes_family(families_dir, ['acme-large'], name='f')
        cases = data_dir / 'cases' / 'smoke-text.toml'
        cases.parent.mkdir(parents=True)
        cases.write_text((FAMILIES_DIR / 'smoke-text' / 'cases.toml').read_text())
        self.context = replace(
            run_context, root=data_dir, private_root=data_dir / 'cases'
        )

        def sources(
            context: RunContext, family: Family
        ) -> list[tuple[SourceJobConfig, Path]]:
            return [
                jobs.job_source(path)
                for path in sorted((data_dir / 'jobs' / family.name).glob('*'))
                if path.is_dir()
            ]

        monkeypatch.setattr(RunContext, 'sources', sources)

    def collect(
        self, family: Family, systems: dict[str, System]
    ) -> collection.Collection:
        return collection.collect(family, systems, self.context)

    @pytest.mark.parametrize('deleted', [False, True], ids=['edited', 'removed'])
    def test_excludes_stale_case_sources_without_rerendering_installed_tasks(
        self, data_dir: Path, deleted: bool
    ) -> None:
        family = votes_family(samples=1)
        system = make_system()
        original = Case(id='a', category='reply', source='synthetic', instruction='Hi.')
        tasks = render_cases(family, [original])
        config = jobs.job(family, [system], tasks, root=bench_data_dir(), sources=[])
        finish_generation(
            config.model_copy(update={'job_name': 'job-1'}), 'Old answer.'
        )
        current = original.model_copy(update={'instruction': 'Write a new answer.'})
        declare_cases(family, [] if deleted else [current])

        outputs = self.collect(family, {system.name: system}).outputs

        assert outputs == []
        assert (tasks[0] / 'instruction.md').read_text() == 'Hi.'

    def test_keeps_only_trials_of_the_current_task(self, data_dir: Path) -> None:
        family = with_settings(
            load_family('smoke-text'), systems=['a', 'b'], reference='a'
        )
        job = data_dir / 'jobs' / 'smoke-text' / 'job-1'
        systems = {side: make_system(name=side) for side in ('a', 'b')}
        agents = {
            side: current_agent(family, system) for side, system in systems.items()
        }
        record_trial(
            job,
            'en-greeting__a-old',
            agents['a'],
            task='smoke-text/en-greeting',
            output='old',
            version=TASK_VERSION,
            family=family,
        )
        for case, side in (
            ('en-greeting', 'b'),
            ('zh-greeting', 'a'),
            ('zh-greeting', 'b'),
        ):
            record_trial(
                job,
                f'{case}__{side}',
                agents[side],
                task=f'smoke-text/{case}',
                output=f'{case} {side}',
                context=AgentContext(metadata={'finish_reason': 'stop'}),
                family=family,
            )

        (data_dir / 'tasks' / 'smoke-text' / 'stray.md').write_text('Stray.')

        outputs = self.collect(family, systems).outputs

        assert {(output.case, output.system) for output in outputs} == {
            ('en-greeting', 'b'),
            ('zh-greeting', 'a'),
            ('zh-greeting', 'b'),
        }

    def test_skips_a_trial_whose_case_no_longer_has_a_task(
        self, data_dir: Path
    ) -> None:
        family = votes_family(samples=1, gates=Gates(script='hans'))
        cases = [
            Case(id='a', category='reply', source='held-out', instruction='Hi.'),
            Case(id='b', category='reply', source='held-out', instruction='Hi.'),
        ]
        for path in render_cases(family, cases):
            record_trial(
                data_dir / 'jobs' / 'f' / 'job-1',
                f'{path.name}__1',
                current_agent(family),
                task=f'f/{path.name}',
                output='你好。',
                instruction=None,
                context=AgentContext(metadata={'finish_reason': 'stop'}),
                family=family,
            )
        render_cases(family, cases[:1])

        (output,) = self.collect(family, {'acme-large': make_system()}).outputs

        assert output.case == 'a'

    def test_keeps_a_trial_whose_instruction_has_a_carriage_return(
        self, data_dir: Path
    ) -> None:
        family = load_family('smoke-text')
        case = Case(
            id='en-greeting',
            category='message',
            source='held-out',
            instruction='Line one.\r\nLine two.',
        )
        (path,) = render_cases(family, [case])
        record_trial(
            data_dir / 'jobs' / 'smoke-text' / 'job-1',
            'en-greeting__1',
            current_agent(family),
            task='smoke-text/en-greeting',
            output='Hi.',
            instruction=None,
            context=AgentContext(metadata={'finish_reason': 'stop'}),
            family=family,
        )

        (output,) = self.collect(family, {'acme-large': make_system()}).outputs

        assert output.case == path.name

    def test_excludes_trials_after_a_generation_timeout_changes(
        self, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        family = load_family('smoke-text')
        render_cases(
            family, load_cases(family, private_root=bench_data_dir() / 'cases')[:1]
        )
        record_trial(
            data_dir / 'jobs' / 'smoke-text' / 'job-1',
            'en-greeting__1',
            current_agent(family),
            task='smoke-text/en-greeting',
            output='Hi.',
            instruction=None,
            context=AgentContext(metadata={'finish_reason': 'stop'}),
            family=family,
        )
        monkeypatch.setattr('assistant_bench.tasks.AGENT_TIMEOUT_SEC', 60.0)

        outputs = self.collect(family, {'acme-large': make_system()}).outputs

        assert outputs == []

    @pytest.mark.parametrize('newline', ['\n', '\r\n', '\r'])
    @pytest.mark.parametrize('eligible', [True, False], ids=['eligible', 'ineligible'])
    def test_extracts_html_without_losing_raw_failed_evidence(
        self, data_dir: Path, newline: str, eligible: bool
    ) -> None:
        family = votes_family(render='html', language=None, gates=Gates())
        document = (FIXTURES / 'html' / 'community-event.html').read_text()
        document = document.replace('\n', newline)
        source = document if eligible else document.replace('</body>', '')
        reply = f'```html{newline}{source}```{newline}'
        case = Case(id='page', category='reply', source='synthetic', instruction='Hi.')
        render_cases(family, [case])
        record_trial(
            data_dir / 'jobs' / 'f' / 'job-1',
            'page__1',
            current_agent(family),
            task='f/page',
            output=reply,
            instruction=None,
            context=AgentContext(metadata={'finish_reason': 'stop'}),
            family=family,
        )

        (output,) = self.collect(family, {'acme-large': make_system()}).outputs

        assert output.text == (document if eligible else reply)
        assert output.gate_failures == ([] if eligible else [Gate.HTML])

    @pytest.mark.parametrize('api', ['openai-chat-completions', 'openai-responses'])
    def test_collects_generations_and_errors(
        self, data_dir: Path, api: str, private_store: SQLiteDatabase
    ) -> None:
        job_dir = data_dir / 'jobs' / 'f' / 'job-1'
        system = make_system(api=api)
        generate = current_agent(VOTES, system)
        record_trial(
            job_dir,
            'b__1',
            generate,
            task='f/b',
            output='Hi.',
            context=AgentContext(
                n_input_tokens=1_000,
                n_cache_tokens=600,
                n_output_tokens=500,
                cost_usd=0.0,
                metadata={
                    'reasoning_tokens': 100,
                    'latency_sec': 1.5,
                    'finish_reason': 'stop',
                    'response_model': 'large-2026',
                    'request_id': 'req-1',
                    'nixos_config_rev': 'rev-a',
                },
            ),
            family=VOTES,
        )
        record_trial(
            job_dir, 'a__1', generate, task='f/a', error='TimeoutError', family=VOTES
        )
        record_accounting(
            private_store,
            'job-1',
            'b__1',
            system,
            MeasuredUsage(1_000, 600, 0, 500, 100),
        )

        outputs = self.collect(VOTES, {system.name: system}).outputs

        assert [(output.case, output.error) for output in outputs] == [
            ('a', 'TimeoutError'),
            ('b', None),
        ]
        assert (outputs[0].cost_usd, outputs[0].gate_failures) == (None, None)
        assert outputs[1].model_dump(exclude={'case', 'error'}) == {
            'trial': 'b__1',
            'job': 'job-1',
            'system': 'acme-large',
            'fingerprint': fingerprint(system),
            'nixos_config_rev': 'rev-a',
            'harness_version': None,
            'text': 'Hi.',
            'reward': None,
            'gate_failures': [],
            'checks': None,
            'finish_reason': 'stop',
            'stop_details': None,
            'input_tokens': 1_000,
            'cached_tokens': 600,
            'cache_write_tokens': 0,
            'output_tokens': 500,
            'reasoning_tokens': 100,
            'cost_usd': pytest.approx((400 * 2.0 + 600 * 0.2 + 500 * 10.0) / 1e6),
            'latency_sec': 1.5,
            'response_models': ('large-2026',),
            'request_id': 'req-1',
        }
        assert read_outputs('f', self.context.state) == outputs

    @pytest.mark.parametrize(
        ('agent', 'kwargs', 'environment', 'verifier'),
        [
            pytest.param({}, {'max_output_tokens': 600}, {}, {}, id='agent-request'),
            pytest.param({'model_name': 'acme/old'}, {}, {}, {}, id='agent-route'),
            pytest.param(
                {'import_path': 'legacy:Generate'}, {}, {}, {}, id='agent-import'
            ),
            pytest.param(
                {},
                {},
                {'type': EnvironmentType.DOCKER, 'import_path': None},
                {},
                id='environment-type',
            ),
            pytest.param(
                {}, {}, {'kwargs': {'legacy': True}}, {}, id='environment-kwargs'
            ),
            pytest.param({}, {}, {}, {'disable': False}, id='verifier-enabled'),
        ],
    )
    def test_prefers_a_trial_with_the_current_execution_config(
        self,
        data_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        agent: dict[str, Any],
        kwargs: dict[str, Any],
        environment: dict[str, Any],
        verifier: dict[str, Any],
    ) -> None:
        family = votes_family(samples=1)
        system = make_system()
        tasks = render_cases(
            family,
            [Case(id='a', category='reply', source='held-out', instruction='Hi.')],
        )
        current = jobs.job(family, [system], tasks, root=bench_data_dir(), sources=[])
        (current_agent_config,) = current.agents
        old = current.model_copy(
            update={
                'job_name': 'job-1',
                'agents': [
                    current_agent_config.model_copy(
                        update=agent | {'kwargs': current_agent_config.kwargs | kwargs}
                    )
                ],
                'environment': current.environment.model_copy(update=environment),
                'verifier': current.verifier.model_copy(update=verifier),
            }
        )
        finish_generation(old, 'old config')
        finish_generation(
            current.model_copy(update={'job_name': 'job-2'}), 'current config'
        )
        gates = Mock(wraps=failed_gates)
        monkeypatch.setattr(collection, 'failed_gates', gates)

        (output,) = self.collect(family, {system.name: system}).outputs

        assert (output.job, output.text) == ('job-2', 'current config')
        gates.assert_called_once_with(family.settings, 'current config', 'stop', 'Hi.')

        shutil.rmtree(data_dir / 'jobs' / 'f' / 'job-2')

        assert self.collect(family, {system.name: system}).outputs == []

    @pytest.mark.parametrize(
        ('boundary', 'change', 'accepted'),
        [
            ('candidate-current', 'relocated', True),
            ('candidate-current', 'changed', False),
            ('candidate-current', 'reordered', False),
            ('origin-trial', 'relocated', True),
            ('origin-trial', 'changed', False),
            ('origin-trial', 'reordered', False),
            ('candidate-current', 'absent', True),
            ('candidate-current', 'empty', False),
        ],
    )
    def test_uses_native_ordered_overlay_content_without_reading_historical_paths(
        self,
        data_dir: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        boundary: str,
        change: str,
        accepted: bool,
    ) -> None:
        family = votes_family(samples=1)
        system = make_system()
        render_cases(
            family,
            [Case(id='a', category='reply', source='synthetic', instruction='Hi.')],
        )
        contents = (
            [b'services:\n  app:\n    command: echo blue\n', b'name: sample\n']
            if change not in {'absent', 'empty'}
            else []
        )

        def environment(
            root: Path, *, altered: bool = False
        ) -> tuple[trial_config.EnvironmentConfig, list[ExtraDockerComposeLock] | None]:
            root.mkdir(parents=True)
            paths: list[Path] = []
            for index, content in enumerate(contents):
                path = root / f'{index}.yaml'
                path.write_bytes(
                    content + b'# Different content.\n'
                    if altered and change == 'changed' and index == 0
                    else content
                )
                paths.append(path)
            if altered and change == 'reordered':
                paths.reverse()
            locks = [
                ExtraDockerComposeLock(
                    path=path, digest=f'sha256:{sha256(path.read_bytes()).hexdigest()}'
                )
                for path in paths
            ]
            return (
                trial_config.EnvironmentConfig(
                    type=EnvironmentType.DOCKER, extra_docker_compose=paths
                ),
                locks if locks or (altered and change == 'empty') else None,
            )

        current, current_locks = environment(tmp_path / 'current')

        def configured_environment(
            systems: list[System], source_root: Path | None = None
        ) -> trial_config.EnvironmentConfig:
            return current

        monkeypatch.setattr(jobs, '_environment', configured_environment)
        agent = current_agent(family, system)

        def recorded(
            name: str, *, altered: bool = False, source: UUID | None = None
        ) -> tuple[UUID, TrialLock]:
            config, locks = environment(tmp_path / 'historical' / name, altered=altered)
            job = data_dir / 'jobs' / family.name / f'job-{name}'
            trial = f'a__{name}'
            identifier = write_trial(
                job,
                trial,
                agent,
                task='f/a',
                output='Archived answer.',
                environment=config,
                verifier_disabled=True,
                source=source,
                context=AgentContext(metadata={'finish_reason': 'stop'}),
            )
            path = job / trial / 'lock.json'
            lock = TrialLock.model_validate_json(path.read_text()).model_copy(
                update={'extra_docker_compose': locks}
            )
            path.write_text(lock.model_dump_json())
            return identifier, lock

        identifier, origin = recorded('origin', altered=True)
        comparison = (
            recorded('regrade', source=identifier)[1]
            if boundary == 'origin-trial'
            else origin.model_copy(
                update={'environment': current, 'extra_docker_compose': current_locks}
            )
        )
        assert (origin == comparison) is accepted
        locks_before = {
            path: path.read_bytes()
            for path in (data_dir / 'jobs' / family.name).glob('*/*/lock.json')
        }
        shutil.rmtree(tmp_path / 'historical')

        outputs = self.collect(family, {system.name: system}).outputs

        assert [(output.job, output.trial, output.text) for output in outputs] == (
            [('job-origin', 'a__origin', 'Archived answer.')] if accepted else []
        )
        assert {path: path.read_bytes() for path in locks_before} == locks_before

    @pytest.mark.parametrize(
        ('errors', 'kept'),
        [
            pytest.param([None, None], 'job-1', id='oldest-success'),
            pytest.param([None, 'TimeoutError'], 'job-1', id='over-a-newer-error'),
            pytest.param(['TimeoutError', None], 'job-2', id='over-an-older-error'),
        ],
    )
    def test_keeps_a_success_over_an_error_then_the_oldest_trial(
        self, data_dir: Path, errors: list[str | None], kept: str
    ) -> None:
        family = votes_family(samples=1)
        system = make_system()
        for index, error in enumerate(errors, start=1):
            record_trial(
                data_dir / 'jobs' / 'f' / f'job-{index}',
                f'a__{index}',
                current_agent(family, system),
                task='f/a',
                output=f'job-{index}',
                context=AgentContext(metadata={'finish_reason': 'stop'}),
                error=error,
                family=family,
            )

        outputs = self.collect(family, {system.name: system}).outputs

        assert [(output.job, output.error) for output in outputs] == [(kept, None)]

    def test_excludes_an_old_success_even_when_the_current_request_failed(
        self, data_dir: Path
    ) -> None:
        family = votes_family(samples=1)
        system = make_system()
        jobs_dir = data_dir / 'jobs' / 'f'
        record_trial(
            jobs_dir / '2026-10-20__10-00-00',
            'a__1',
            current_agent(family, system),
            task='f/a',
            error='TimeoutError',
            family=family,
        )
        record_trial(
            jobs_dir / '2026-10-21__10-00-00',
            'a__2',
            current_agent(family, system.model_copy(update={'max_output_tokens': 600})),
            task='f/a',
            output='old request',
            context=AgentContext(metadata={'finish_reason': 'stop'}),
            family=family,
        )

        outputs = self.collect(family, {system.name: system}).outputs

        assert [(output.job, output.error, output.text) for output in outputs] == [
            ('2026-10-20__10-00-00', 'TimeoutError', None)
        ]

    @pytest.mark.parametrize(
        'change',
        [
            pytest.param({'name': 'old-name'}, id='renamed'),
            pytest.param({'route': 'acme/old'}, id='new-fingerprint'),
            pytest.param({'api': 'openai-responses'}, id='changed-transport'),
        ],
    )
    def test_excludes_a_previous_system_identity(
        self, data_dir: Path, change: dict[str, str]
    ) -> None:
        family = votes_family(samples=1)
        system = make_system()
        previous = system.model_copy(update=change)
        for job, recorded in (('job-1', previous), ('job-2', system)):
            record_trial(
                data_dir / 'jobs' / 'f' / job,
                'a__1',
                current_agent(family, recorded),
                task='f/a',
                output=job,
                family=family,
            )

        outputs = self.collect(family, {system.name: system}).outputs

        assert [
            (output.system, output.fingerprint, output.text) for output in outputs
        ] == [(system.name, fingerprint(system), 'job-2')]

    def test_gates_each_output_against_its_case_instruction(
        self, data_dir: Path
    ) -> None:
        family = votes_family(samples=1, language='zh', gates=Gates(script='hans'))
        generate = current_agent(family)
        stop = AgentContext(metadata={'finish_reason': 'stop'})
        for case, output in (('a', '臺北今天很热。'), ('b', '臺北今天很熱。')):
            record_trial(
                data_dir / 'jobs' / 'f' / 'job-1',
                f'{case}__1',
                generate,
                task=f'f/{case}',
                output=output,
                instruction='介绍一下臺北的天气。',
                context=stop,
                family=family,
            )

        outputs = self.collect(family, {'acme-large': make_system()}).outputs

        assert [(output.case, output.gate_failures) for output in outputs] == [
            ('a', []),
            ('b', [Gate.SCRIPT]),
        ]

    def test_reads_no_instruction_without_the_script_gate(self, data_dir: Path) -> None:
        declare_cases(
            VOTES,
            [Case(id='a', category='reply', source='synthetic', instruction='Hi.')],
        )
        task = data_dir / 'tasks' / 'f' / 'a'
        task.mkdir(parents=True)
        (task / 'task.toml').write_text(
            render_task_config('f/a', 'Hi.').model_dump_toml()
        )
        record_trial(
            data_dir / 'jobs' / 'f' / 'job-1',
            'a__1',
            current_agent(VOTES),
            task='f/a',
            output='Hi.',
            instruction=None,
            context=AgentContext(metadata={'finish_reason': 'stop'}),
            family=VOTES,
        )

        (output,) = self.collect(VOTES, {'acme-large': make_system()}).outputs

        assert output.gate_failures == []

    @pytest.mark.parametrize(
        ('current', 'kept'),
        [
            pytest.param('1.0.0', True, id='same-version'),
            pytest.param('1.0.3', True, id='patch-bump'),
            pytest.param('1.0.3-rc.1', True, id='patch-bump-to-a-pre-release'),
            pytest.param('1.0.0-rc.1', False, id='pre-release-alone'),
            pytest.param('1.0.0+build.2', False, id='build-metadata-alone'),
            pytest.param('1.1.0', False, id='minor-bump'),
            pytest.param('2.0.0', False, id='major-bump'),
        ],
    )
    def test_keeps_trials_of_current_tasks_at_a_version_harbor_reuses(
        self, families_dir: Path, data_dir: Path, current: str, kept: bool
    ) -> None:
        family = voted_tasks(families_dir, 'echo')
        break_setting(case_task(family, 'echo'), ('task', 'version'), current)
        for task in ('echo', 'deleted'):
            record_trial(
                data_dir / 'jobs' / 'f' / 'job-1',
                f'{task}__1',
                current_agent(family),
                task=f'f/{task}',
                reward=1.0,
                instruction=None,
                version='1.0.0',
                family=family,
            )

        outputs = self.collect(family, {'acme-large': make_system()}).outputs

        assert [output.case for output in outputs] == (['echo'] if kept else [])

    @pytest.mark.parametrize(
        ('reward', 'error', 'verdict', 'diff'),
        [
            pytest.param(1.0, None, [], True, id='passed'),
            pytest.param(0.0, None, [Gate.TESTS], True, id='failed'),
            pytest.param(0.5, None, [Gate.TESTS], True, id='partial'),
            pytest.param(1.0, 'AgentTimeoutError', [], True, id='graded-timeout'),
            pytest.param(None, 'RuntimeError', None, False, id='never-graded'),
        ],
    )
    def test_gates_a_voted_task_by_its_tests_and_diffs_its_workspace(
        self,
        families_dir: Path,
        data_dir: Path,
        reward: float | None,
        error: str | None,
        verdict: list[Gate] | None,
        diff: bool,
    ) -> None:
        family = voted_tasks(families_dir, 'echo')
        base = case_task(family, 'echo') / 'environment' / 'app'
        base.mkdir()
        (base / 'README.md').write_text('Echo.\n')
        record_trial(
            data_dir / 'jobs' / 'f' / 'job-1',
            'echo__1',
            current_agent(family),
            task='f/echo',
            reward=reward,
            error=error,
            instruction=None,
            version='1.0.0',
            workspace={'README.md': 'Echo.\n', 'greeting.txt': 'hello\n'},
            family=family,
        )

        (output,) = self.collect(family, {'acme-large': make_system()}).outputs

        assert (output.error, output.reward, output.gate_failures) == (
            error,
            reward,
            verdict,
        )
        assert output.text == (GREETING_DIFF if diff else None)

    @pytest.mark.parametrize(
        ('attempts', 'job', 'error', 'reward', 'verdict'),
        [
            pytest.param(
                [
                    (None, 'InternalServerError'),
                    (None, 'CancelledError'),
                    (1.0, 'AgentTimeoutError'),
                ],
                'job-3',
                'AgentTimeoutError',
                1.0,
                [],
                id='graded-error-over-ungraded-errors',
            ),
            pytest.param(
                [
                    (0.5, 'AgentTimeoutError'),
                    (1.0, 'AgentTimeoutError'),
                    (0.0, 'AgentTimeoutError'),
                ],
                'job-1',
                'AgentTimeoutError',
                0.5,
                [Gate.TESTS],
                id='oldest-graded-error',
            ),
            pytest.param(
                [(0.0, 'AgentTimeoutError'), (0.0, None)],
                'job-2',
                None,
                0.0,
                [Gate.TESTS],
                id='success-over-graded-error',
            ),
        ],
    )
    def test_ranks_a_success_then_a_graded_then_an_ungraded_error_oldest_first(
        self,
        families_dir: Path,
        data_dir: Path,
        attempts: list[tuple[float | None, str | None]],
        job: str,
        error: str | None,
        reward: float,
        verdict: list[Gate],
    ) -> None:
        family = voted_tasks(families_dir, 'echo')
        agent = current_agent(family)
        for index, (attempt_reward, attempt_error) in enumerate(attempts, start=1):
            record_trial(
                data_dir / 'jobs' / 'f' / f'job-{index}',
                f'echo__{index}',
                agent,
                task='f/echo',
                reward=attempt_reward,
                error=attempt_error,
                instruction=None,
                version='1.0.0',
                workspace={'greeting.txt': 'hello\n'},
                family=family,
            )

        (output,) = self.collect(family, {'acme-large': make_system()}).outputs

        assert (
            output.job,
            output.error,
            output.reward,
            output.gate_failures,
        ) == (job, error, reward, verdict)

    @pytest.mark.parametrize(
        ('regrades', 'verdict'),
        [
            pytest.param(1, [], id='regrade'),
            pytest.param(2, [Gate.TESTS], id='regrade-of-a-regrade'),
        ],
    )
    @pytest.mark.parametrize('origin_entries', [0, 1, 2])
    def test_gives_a_regrade_only_its_uniquely_accounted_origin_usage(
        self,
        families_dir: Path,
        data_dir: Path,
        regrades: int,
        verdict: list[Gate],
        origin_entries: int,
        private_store: SQLiteDatabase,
    ) -> None:
        rewards = [0.0, 1.0, 0.5]
        family = voted_tasks(families_dir, 'echo')
        system = make_system()
        agent = current_agent(family, system)
        origin = data_dir / 'jobs' / 'f' / 'job-1'
        source = record_trial(
            origin,
            'echo__origin',
            agent,
            task='f/echo',
            reward=rewards[0],
            instruction=None,
            version='1.0.0',
            workspace={'greeting.txt': 'hello\n'},
            context=AgentContext(
                n_input_tokens=1_000,
                n_output_tokens=500,
                metadata={'harbor_version': '0.24.0'},
            ),
            agent_execution=TimingInfo(
                started_at=START, finished_at=START + timedelta(seconds=30)
            ),
            family=family,
        )
        (origin / 'echo__origin' / 'artifacts' / 'app' / 'link').symlink_to(
            'greeting.txt'
        )
        for minor in range(1, regrades + 1):
            source = record_trial(
                data_dir / 'jobs' / 'f' / f'job-{minor + 1}',
                f'echo__regrade-{minor}',
                agent,
                task='f/echo',
                reward=rewards[minor],
                instruction=None,
                version=f'1.{minor}.0',
                workspace={'greeting.txt': 'hello\n', 'link': 'hello\n'},
                context=AgentContext(n_input_tokens=1, n_output_tokens=1),
                source=source,
                family=family,
            )
        break_setting(case_task(family, 'echo'), ('task', 'version'), f'1.{regrades}.0')
        frozen = system.model_copy(
            update={'price': Price(input=7, output=30, cache_read=1)}
        )
        for index in range(origin_entries):
            record_accounting(
                private_store,
                origin.name,
                'echo__origin',
                frozen,
                MeasuredUsage(1_000, 0, 0, 500, None),
                seconds=index,
            )
        record_accounting(
            private_store,
            f'job-{regrades + 1}',
            f'echo__regrade-{regrades}',
            system,
            MeasuredUsage(99_000, 0, 0, 99_000, None),
            seconds=100,
        )

        (output,) = self.collect(family, {system.name: system}).outputs

        assert output.model_dump(
            include={
                'job',
                'trial',
                'text',
                'input_tokens',
                'cost_usd',
                'latency_sec',
                'harness_version',
                'reward',
                'gate_failures',
            }
        ) == {
            'job': 'job-1',
            'trial': 'echo__origin',
            'text': GREETING_DIFF + 'diff --git a/link b/link\nnew file mode 120000\n'
            '--- /dev/null\n+++ b/link\n@@ -0,0 +1 @@\n+greeting.txt\n'
            '\\ No newline at end of file\n',
            'input_tokens': 1_000 if origin_entries == 1 else None,
            'cost_usd': pytest.approx(0.022) if origin_entries == 1 else None,
            'latency_sec': 30.0,
            'harness_version': '0.24.0',
            'reward': rewards[regrades],
            'gate_failures': verdict,
        }

    @pytest.mark.parametrize('changed', ['agent', 'environment'])
    def test_regrading_cannot_turn_an_old_generation_contract_into_a_current_one(
        self, families_dir: Path, data_dir: Path, changed: str
    ) -> None:
        family = voted_tasks(families_dir, 'echo')
        system = make_system()
        config = jobs.job(
            family,
            jobs.family_systems(family, [system]),
            [],
            root=bench_data_dir(),
            sources=[],
        )
        agent = config.agents[0]
        old_agent = (
            agent.model_copy(update={'kwargs': agent.kwargs | {'max_turns': 40}})
            if changed == 'agent'
            else agent
        )
        old_environment = (
            config.environment.model_copy(update={'force_build': True})
            if changed == 'environment'
            else config.environment
        )
        source = write_trial(
            data_dir / 'jobs' / 'f' / 'job-1',
            'echo__origin',
            old_agent,
            task='f/echo',
            reward=0.0,
            instruction=None,
            version='1.0.0',
            workspace={'greeting.txt': 'hello\n'},
            environment=old_environment,
            verifier_disabled=config.verifier.disable,
        )
        record_trial(
            data_dir / 'jobs' / 'f' / 'job-2',
            'echo__regrade',
            agent,
            task='f/echo',
            reward=1.0,
            instruction=None,
            version='1.1.0',
            workspace={'greeting.txt': 'hello\n'},
            source=source,
            family=family,
        )
        break_setting(case_task(family, 'echo'), ('task', 'version'), '1.1.0')

        outputs = self.collect(family, {system.name: system}).outputs

        assert outputs == []

    @pytest.mark.parametrize(
        ('source_known', 'expected'),
        [
            pytest.param(
                True,
                ('job-1', 'echo__origin', 'VerifierTimeoutError', None, None),
                id='errored-regrade',
            ),
            pytest.param(
                False,
                ('job-2', 'echo__regrade', 'VerifierTimeoutError', None, None),
                id='source-missing-from-the-index',
            ),
        ],
    )
    def test_takes_a_regrade_error_and_ends_its_chain_at_a_missing_source(
        self,
        families_dir: Path,
        data_dir: Path,
        source_known: bool,
        expected: tuple[str, str, str, float | None, list[Gate] | None],
    ) -> None:
        family = voted_tasks(families_dir, 'echo')
        agent = current_agent(family)
        source = record_trial(
            data_dir / 'jobs' / 'f' / 'job-1',
            'echo__origin',
            agent,
            task='f/echo',
            reward=1.0,
            instruction=None,
            version='1.0.0',
            family=family,
        )
        if not source_known:
            shutil.rmtree(data_dir / 'jobs' / 'f' / 'job-1')
        record_trial(
            data_dir / 'jobs' / 'f' / 'job-2',
            'echo__regrade',
            agent,
            task='f/echo',
            error='VerifierTimeoutError',
            instruction=None,
            version='1.1.0',
            source=source,
            family=family,
        )
        break_setting(case_task(family, 'echo'), ('task', 'version'), '1.1.0')

        (output,) = self.collect(family, {'acme-large': make_system()}).outputs

        assert (
            output.job,
            output.trial,
            output.error,
            output.reward,
            output.gate_failures,
        ) == expected

    def test_keeps_one_output_for_a_solution_harbor_regraded_twice(
        self,
        families_dir: Path,
        data_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        family = with_settings(voted_tasks(families_dir, 'echo'), samples=2)
        agent = current_agent(family)
        origin = record_trial(
            data_dir / 'jobs' / 'f' / 'job-1',
            'echo__origin',
            agent,
            task='f/echo',
            reward=1.0,
            instruction=None,
            version='1.0.0',
            workspace={'greeting.txt': 'hello\n'},
            family=family,
        )
        for job, trial in (('job-2', 'echo__first'), ('job-3', 'echo__second')):
            record_trial(
                data_dir / 'jobs' / 'f' / job,
                trial,
                agent,
                task='f/echo',
                reward=1.0,
                instruction=None,
                version='1.1.0',
                workspace={'greeting.txt': 'hello\n'},
                source=origin,
                family=family,
            )
        break_setting(case_task(family, 'echo'), ('task', 'version'), '1.1.0')
        diff = Mock(wraps=workspace_diff)
        monkeypatch.setattr(collection, 'workspace_diff', diff)

        outputs = self.collect(family, {'acme-large': make_system()}).outputs

        assert [(output.job, output.trial) for output in outputs] == [
            ('job-1', 'echo__origin')
        ]
        assert (outputs[0].text, outputs[0].reward, outputs[0].gate_failures) == (
            GREETING_DIFF,
            1.0,
            [],
        )
        diff.assert_called_once_with(
            case_task(family, 'echo') / 'environment' / 'app',
            data_dir / 'jobs' / 'f' / 'job-1' / 'echo__origin' / 'artifacts' / 'app',
        )

    def test_prefers_the_current_request_of_a_task_family_under_its_harness(
        self, families_dir: Path, data_dir: Path
    ) -> None:
        family = voted_tasks(families_dir, 'echo')
        system = make_system()
        (run,) = jobs.family_systems(family, [system])
        config = jobs.job(
            family,
            [run],
            [case_task(family, 'echo')],
            root=bench_data_dir(),
            sources=[],
        )
        agent = config.agents[0]
        current = agent.kwargs
        for job, kwargs in (
            ('job-1', {**current, 'max_turns': 99}),
            ('job-2', current),
        ):
            finish_job(
                config.model_copy(
                    update={
                        'job_name': job,
                        'agents': [agent.model_copy(update={'kwargs': kwargs})],
                    }
                )
            )

        (output,) = self.collect(family, {system.name: system}).outputs

        assert output.job == 'job-2'

    def test_excludes_a_system_the_family_no_longer_lists(self, data_dir: Path) -> None:
        system = make_system()
        family = votes_family(systems=['other'], reference='other', samples=1)
        tasks = render_cases(
            family,
            [Case(id='c', category='reply', source='held-out', instruction='Hi.')],
        )
        for job, bound in (('job-1', 100), ('job-2', 300)):
            config = jobs.job(
                family,
                [system.model_copy(update={'max_output_tokens': bound})],
                tasks,
                root=bench_data_dir(),
                sources=[],
            )
            finish_generation(
                config.model_copy(update={'job_name': job}), f'reply at {bound}'
            )

        outputs = self.collect(family, {system.name: system}).outputs

        assert outputs == []

    @pytest.mark.parametrize(
        ('metadata', 'timing', 'latency'),
        [
            pytest.param(
                {'latency_sec': 1.5},
                TimingInfo(started_at=START, finished_at=START + timedelta(seconds=9)),
                1.5,
                id='recorded-by-the-agent',
            ),
            pytest.param(
                {},
                TimingInfo(
                    started_at=START, finished_at=START + timedelta(seconds=12.25)
                ),
                12.25,
                id='agent-execution',
            ),
            pytest.param({}, TimingInfo(started_at=START), None, id='unfinished'),
            pytest.param({}, None, None, id='no-timing'),
        ],
    )
    def test_takes_the_latency_from_the_agent_or_its_execution_time(
        self,
        data_dir: Path,
        metadata: dict[str, Any],
        timing: TimingInfo | None,
        latency: float | None,
    ) -> None:
        record_trial(
            data_dir / 'jobs' / 'f' / 'job-1',
            'a__1',
            current_agent(VOTES),
            task='f/a',
            context=AgentContext(metadata=metadata),
            agent_execution=timing,
            family=VOTES,
        )

        (output,) = self.collect(VOTES, {'acme-large': make_system()}).outputs

        assert output.latency_sec == latency

    def test_lists_each_model_a_terminal_agents_calls_reported(
        self, data_dir: Path
    ) -> None:
        models = [f'model-{index}' for index in range(8)]
        calls = [
            *({'response_model': model} for model in reversed(models)),
            {'response_model': None},
            {'response_model': models[-1]},
        ]
        record_trial(
            data_dir / 'jobs' / 'f' / 'job-1',
            'a__1',
            current_agent(),
            task='f/a',
            context=AgentContext(metadata={'calls': calls}),
        )

        (output,) = self.collect(VOTES, {'acme-large': make_system()}).outputs

        assert output.response_models == tuple(models)

    def test_names_an_unknown_harness(self, families_dir: Path) -> None:
        family = with_settings(voted_tasks(families_dir), harness='unknown')
        system = make_system()

        with pytest.raises(ValueError) as error:
            self.collect(family, {system.name: system})

        assert str(error.value) == (
            'Family `f`: harness `unknown` is not one of `generate`, '
            '`terminus-2`, `claude-code`, `codex`.'
        )

    @pytest.mark.parametrize('api', ['openai-chat-completions', 'openai-responses'])
    def test_leaves_a_current_system_without_a_price_unpriced(
        self, data_dir: Path, api: str
    ) -> None:
        system = make_system(price=None, api=api)
        record_trial(
            data_dir / 'jobs' / 'f' / 'job-1',
            'a__1',
            current_agent(VOTES, system),
            task='f/a',
            context=AgentContext(
                n_input_tokens=10,
                n_cache_tokens=8,
                n_output_tokens=5,
                cost_usd=0.0,
            ),
            family=VOTES,
        )

        (output,) = self.collect(VOTES, {system.name: system}).outputs

        assert output.system == system.name
        assert output.cost_usd is None

    def test_names_a_trial_whose_result_is_truncated(self, data_dir: Path) -> None:
        trial = data_dir / 'jobs' / 'f' / 'job-1' / 'a__1'
        record_trial(
            trial.parent, trial.name, current_agent(VOTES), task='f/a', family=VOTES
        )
        result = trial / 'result.json'
        result.write_text(result.read_text()[:20])

        with pytest.raises(ValueError) as error:
            self.collect(VOTES, {'acme-large': make_system()})

        assert str(error.value).startswith(
            f"Source trial '{trial.resolve()}' has an unreadable result.json"
        )

    @pytest.mark.parametrize('completion', ['stop', 'length', None, 'error'])
    def test_recollection_regrades_without_generation_changes(
        self,
        families_dir: Path,
        data_dir: Path,
        completion: str | None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        write_checkers_family(families_dir)
        family = load_family('instructions-format')
        cases = load_cases(family, private_root=bench_data_dir() / 'cases')
        system = make_system(name='acme')
        paths = render_tasks(family, cases, root=bench_data_dir())
        finish_generation(
            jobs.job(family, [system], paths[:1], root=bench_data_dir(), sources=[]),
            'Wait…',
        )
        (result_file,) = (data_dir / 'jobs' / family.name).glob('*/*/result.json')
        payload = json.loads(result_file.read_text())
        payload['agent_result']['metadata']['finish_reason'] = (
            'stop' if completion == 'error' else completion
        )
        if completion == 'error':
            payload['exception_info'] = {
                'exception_type': 'AgentError',
                'exception_message': 'failed',
                'exception_traceback': '',
                'occurred_at': START.isoformat(),
            }
        result_file.write_text(json.dumps(payload))
        inputs = jobs.current_inputs(family, [system])
        versions = current_versions(family, {case.id: case for case in cases})
        first = self.collect(family, {'acme': system}).outputs
        assert first[0].checks is not None and not first[0].checks.passed

        assert cases[0].checks is not None
        cases[0].checks = cases[0].checks.model_copy(
            update={'rules': ['curly-quotes'], 'targets': ['curly-quotes']}
        )
        (families_dir / family.name / 'cases.toml').write_text(
            tomli_w.dumps(
                {'cases': [case.model_dump(exclude_none=True) for case in cases]}
            )
        )
        monkeypatch.setattr(checkers, 'REVISION', 'format-2')
        second = self.collect(family, {'acme': system}).outputs

        assert second[0].checks is not None
        assert second[0].checks.revision == 'format-2'
        assert second[0].checks.passed == (completion == 'stop')
        assert first[0].trial == second[0].trial
        assert first[0].fingerprint == second[0].fingerprint
        assert jobs.current_inputs(family, [system]) == inputs
        assert current_versions(family, {case.id: case for case in cases}) == versions


class TestReadTrials:
    @pytest.fixture(autouse=True)
    def current_sources(self, families_dir: Path) -> None:
        write_votes_family(families_dir, ['acme-large'], name='f')

    def test_skips_a_removed_case_without_reading_its_installed_instruction(
        self, data_dir: Path
    ) -> None:
        case = Case(id='a', category='reply', source='synthetic', instruction='Hi.')
        (task,) = render_cases(VOTES, [case])
        job = data_dir / 'jobs' / 'f' / 'job-1'
        write_trial(
            job,
            'a__1',
            current_agent(),
            task='f/a',
            output='An old reply.',
            instruction=None,
        )
        declare_cases(VOTES, [])
        (task / 'instruction.md').unlink()

        outputs = collection.read_trials(
            VOTES, job, private_root=bench_data_dir() / 'cases'
        )

        assert outputs == []

    def test_returns_one_output_per_finished_trial(self, data_dir: Path) -> None:
        declare_cases(
            VOTES,
            [Case(id='a', category='reply', source='synthetic', instruction='Hi.')],
        )
        job = data_dir / 'jobs' / 'f' / 'job-1'
        for attempt in range(3):
            write_trial(
                job,
                f'a__{attempt}',
                trial_config.AgentConfig(import_path='x:Y', kwargs={'system': 's'}),
                task='f/a',
                output=f'Reply {attempt}.',
            )

        outputs = collection.read_trials(
            VOTES, job, private_root=bench_data_dir() / 'cases'
        )

        assert sorted(
            (output.case, output.system, output.text) for output in outputs
        ) == [('a', 's', f'Reply {attempt}.') for attempt in range(3)]
