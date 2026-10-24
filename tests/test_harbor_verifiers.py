import shutil
from collections import Counter
from pathlib import Path

import pytest

from assistant_bench import jobs
from assistant_bench.families import case_task, load_family


pytestmark = [pytest.mark.docker, pytest.mark.usefixtures('export')]


class TestStandaloneTasks:
    def test_refactor_task_runs_from_its_standalone_directory(
        self, tmp_path: Path
    ) -> None:
        family = load_family('coding-repo')
        name = 'refactor-parsers'
        source = case_task(family, name)
        target = tmp_path / 'standalone' / source.name
        shutil.copytree(source, target, symlinks=True)

        directory = jobs.run_job(jobs.check_job(family, [target]))

        outputs = jobs.read_trials(family, directory, {})
        assert Counter(
            (output.case, output.system, output.error, output.reward)
            for output in outputs
        ) == Counter(
            {
                (name, agent, None, reward): jobs.CHECK_ATTEMPTS
                for agent, reward in (('oracle', 1.0), ('nop', 0.0))
            }
        )
