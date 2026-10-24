from pathlib import Path

import pytest
from harbor.models.task.config import StepConfig, TaskConfig
from harbor.publisher.packager import Packager

from assistant_bench.arena.sources import prove_task


def native_digest(directory: Path) -> str:
    value, _ = Packager.compute_content_hash(directory)
    return f'sha256:{value}'


@pytest.fixture
def task(tmp_path: Path) -> Path:
    directory = tmp_path / 'task'
    (directory / 'environment').mkdir(parents=True)
    (directory / 'tests').mkdir()
    (directory / 'instruction.md').write_text('Repair the greeting.\n')
    (directory / 'task.toml').write_text(TaskConfig().model_dump_toml())
    (directory / 'environment/Dockerfile').write_text('FROM scratch\n')
    (directory / 'tests/test.sh').write_text('exit 0\n')
    return directory


class TestProveTask:
    def test_regrading_changes_the_full_hash_without_changing_agent_inputs(
        self, task: Path
    ) -> None:
        before = prove_task(task, native_digest(task))
        (task / 'tests/test.sh').write_text('test -s /app/greeting.txt\n')

        with pytest.raises(ValueError, match='recorded Harbor digest'):
            prove_task(task, before.task_digest)

        after = prove_task(task, native_digest(task))

        assert after.task_digest != before.task_digest
        assert after.source_digest == before.source_digest
        assert after.instruction == before.instruction == 'Repair the greeting.\n'

    @pytest.mark.parametrize(
        'input_name', ['instruction', 'workspace', 'agent', 'environment']
    )
    def test_agent_input_changes_require_a_new_source_identity(
        self, task: Path, input_name: str
    ) -> None:
        expected = native_digest(task)
        before = prove_task(task, expected)

        match input_name:
            case 'instruction':
                (task / 'instruction.md').write_text('Repair the farewell.\n')
            case 'workspace':
                (task / 'environment/greeting.txt').write_text('hello\n')
            case 'agent' | 'environment':
                config = TaskConfig.model_validate_toml(
                    (task / 'task.toml').read_text()
                )

                if input_name == 'agent':
                    config.agent.timeout_sec = 30
                else:
                    config.environment.env = {'GREETING': 'hello'}

                (task / 'task.toml').write_text(config.model_dump_toml())
            case _:
                raise AssertionError(input_name)

        with pytest.raises(ValueError, match='recorded Harbor digest'):
            prove_task(task, expected)

        after = prove_task(task, native_digest(task))

        assert after.source_digest != before.source_digest

    @pytest.mark.parametrize('target', ['file', 'directory'])
    def test_refuses_environment_symlinks_even_with_a_matching_native_hash(
        self, task: Path, tmp_path: Path, target: str
    ) -> None:
        if target == 'file':
            outside = tmp_path / 'greeting.txt'
            outside.write_text('hello\n')
            (task / 'environment/greeting.txt').symlink_to(outside)
        else:
            environment = task / 'environment'
            outside = tmp_path / 'outside'
            environment.rename(outside)
            environment.symlink_to(outside, target_is_directory=True)

        with pytest.raises(ValueError, match='symlink targets'):
            prove_task(task, native_digest(task))

    @pytest.mark.parametrize('ignored', ['instruction.md', 'environment/greeting.txt'])
    def test_refuses_agent_inputs_excluded_from_the_native_package(
        self, task: Path, ignored: str
    ) -> None:
        (task / 'environment/greeting.txt').write_text('hello\n')
        (task / '.gitignore').write_text(f'{ignored}\n')

        with pytest.raises(ValueError, match='native packaged byte provenance'):
            prove_task(task, native_digest(task))

    def test_refuses_multistep_tasks_before_deriving_a_single_step_identity(
        self, task: Path
    ) -> None:
        config = TaskConfig(steps=[StepConfig(name='first')])
        (task / 'task.toml').write_text(config.model_dump_toml())

        with pytest.raises(ValueError, match='Multistep'):
            prove_task(task, native_digest(task))
