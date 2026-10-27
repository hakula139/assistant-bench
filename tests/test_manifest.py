from pathlib import Path

import pytest

from assistant_bench import manifest
from assistant_bench.harnesses import fingerprint
from support import make_system, write_case_family, write_task_family


class TestRunnableSystems:
    def test_names_an_undefined_system_and_its_family(self, families_dir: Path) -> None:
        write_case_family(families_dir, ['missing'], name='writing')

        with pytest.raises(
            ValueError, match='Family `writing`.*`missing`.*systems.toml'
        ):
            manifest.runnable_systems({})

    def test_resolves_only_listed_systems_under_each_family_harness(
        self, families_dir: Path
    ) -> None:
        write_case_family(families_dir, ['large'], name='writing')
        write_task_family(families_dir, 'echo', name='coding', systems=('large',))
        write_task_family(families_dir, name='smoke')
        system = make_system(name='large')

        runnable = manifest.runnable_systems(
            {'large': system, 'author-only': make_system(name='author-only')}
        )

        assert runnable == {
            'coding': [system.model_copy(update={'harness': 'terminus-2'})],
            'smoke': [],
            'writing': [system],
        }


class TestCurrentFingerprints:
    def test_fingerprints_each_system_under_its_family_harness(
        self, families_dir: Path
    ) -> None:
        write_case_family(families_dir, ['large'], name='writing')
        write_task_family(families_dir, 'echo', name='coding', systems=('large',))
        system = make_system(name='large')

        current = manifest.current_fingerprints({'large': system})

        assert current == {
            'coding/large': fingerprint(
                system.model_copy(update={'harness': 'terminus-2'})
            ),
            'writing/large': fingerprint(system),
        }
        assert current['coding/large'] != current['writing/large']
