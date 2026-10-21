import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from assistant_bench import families
from assistant_bench.families import (
    Family,
    TaskTestsSettings,
    VotesSettings,
    load_cases,
    load_family,
)


@pytest.fixture
def families_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / 'families'
    monkeypatch.setattr(families, 'FAMILIES_DIR', directory)
    return directory


def write_family(
    root: Path, name: str, settings: str, *, cases: bool, tasks: bool
) -> None:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / 'family.toml').write_text(settings)
    if cases:
        (directory / 'cases.jsonl').write_text('')
    if tasks:
        (directory / 'tasks').mkdir()


class TestLoadFamily:
    def test_reads_a_case_family_scored_by_votes(self) -> None:
        family = load_family('smoke-text')

        assert family.source == 'cases'
        assert family.settings == VotesSettings(
            scorer='votes',
            systems=[
                'opus-5.5',
                'gpt-6.1-sol',
                'gemini-4-argon',
                'gemini-3.8-flash',
                'deepseek-v4.1-flash',
            ],
            samples=1,
            max_output_tokens=512,
        )

    def test_reads_a_task_family_scored_by_tests(self) -> None:
        assert load_family('smoke') == Family(
            name='smoke',
            source='tasks',
            settings=TaskTestsSettings(scorer='tests', samples=1),
        )

    @pytest.mark.parametrize(('cases', 'tasks'), [(True, True), (False, False)])
    def test_requires_exactly_one_task_source(
        self, families_dir: Path, cases: bool, tasks: bool
    ) -> None:
        write_family(
            families_dir,
            'f',
            'scorer = "tests"\nsamples = 1\n',
            cases=cases,
            tasks=tasks,
        )

        with pytest.raises(ValueError, match='expected exactly one of cases.jsonl'):
            load_family('f')

    def test_requires_an_output_bound_for_cases(self, families_dir: Path) -> None:
        write_family(
            families_dir,
            'f',
            'scorer = "votes"\nsamples = 1\n',
            cases=True,
            tasks=False,
        )

        with pytest.raises(ValueError, match='case families need max_output_tokens'):
            load_family('f')

    def test_rejects_an_unknown_scorer(self, families_dir: Path) -> None:
        write_family(
            families_dir,
            'f',
            'scorer = "judges"\nsamples = 1\n',
            cases=False,
            tasks=True,
        )

        with pytest.raises(ValidationError):
            load_family('f')


class TestLoadCases:
    def test_appends_local_cases_to_the_committed_ones(self, data_dir: Path) -> None:
        local = data_dir / 'cases' / 'smoke-text.jsonl'
        local.parent.mkdir(parents=True)
        local.write_text(
            json.dumps(
                {
                    'id': 'zh-local',
                    'category': 'reply',
                    'source': 'held-out',
                    'instruction': 'Answer.',
                }
            )
            + '\n'
        )

        cases = load_cases(load_family('smoke-text'))

        assert [case.id for case in cases] == ['en-greeting', 'zh-greeting', 'zh-local']
        assert cases[-1].source == 'held-out'
