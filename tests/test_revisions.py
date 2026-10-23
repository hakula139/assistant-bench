import subprocess
import textwrap
from typing import Any

import pytest

from assistant_bench import revisions
from assistant_bench.paths import REPO_ROOT
from support import EXPORT_FILE, make_system


SYSTEMS = textwrap.dedent(
    """\
    [large]
    model = "acme-large"
    """
)


class TestSystemsAt:
    @staticmethod
    def fake_git(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
        outputs = {
            'rev-parse': 'abc123',
            'show': SYSTEMS,
            'build': str(EXPORT_FILE),
        }
        calls: list[tuple[str, ...]] = []

        def run(
            args: tuple[str, ...], **kwargs: Any
        ) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            assert kwargs == {
                'check': True,
                'stdout': subprocess.PIPE,
                'text': True,
                'cwd': REPO_ROOT,
            }
            return subprocess.CompletedProcess(args, 0, stdout=f'{outputs[args[1]]}\n')

        monkeypatch.setattr(subprocess, 'run', run)
        return calls

    def test_resolves_the_systems_against_the_export_of_the_revision(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self.fake_git(monkeypatch)

        systems = revisions.systems_at('origin/main')

        assert systems == {'large': make_system(name='large')}
        assert calls == [
            ('git', 'rev-parse', '--verify', 'origin/main^{commit}'),
            ('git', 'show', 'abc123:systems.toml'),
            (
                'nix',
                'build',
                '--no-link',
                '--print-out-paths',
                f'git+file:{REPO_ROOT}?rev=abc123#nixos-config',
            ),
        ]


class TestChangedFingerprints:
    def test_names_only_systems_that_kept_their_name(self) -> None:
        base = {'kept': make_system(), 'changed': make_system(), 'gone': make_system()}
        head = {
            'kept': make_system(),
            'changed': make_system(effort='high'),
            'new': make_system(effort='low'),
        }

        assert revisions.changed_fingerprints(base, head) == ['changed']
