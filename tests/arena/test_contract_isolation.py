import subprocess
import sys
from textwrap import dedent

from assistant_bench.paths import REPO_ROOT


class TestOfflineContracts:
    def test_real_admin_commands_validate_without_loading_server_implementations(
        self,
    ) -> None:
        script = dedent("""
            import importlib.abc
            import sys

            forbidden = {
                'assistant_bench_arena.ledger',
                'assistant_bench_arena.reads',
                'assistant_bench_arena.investigation',
                'assistant_bench_arena.maintenance',
                'assistant_bench_arena.schema',
                'assistant_bench_arena.management_domain',
                'assistant_bench_arena.worker',
            }

            class Boundary(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if any(fullname == name or fullname.startswith(name + '.')
                           for name in forbidden):
                        raise AssertionError(
                            'Offline command imported server implementation: '
                            + fullname
                        )
                    return None

            sys.meta_path.insert(0, Boundary())

            from assistant_bench.arena import commands
            from assistant_bench_arena import management_models as command
            from assistant_bench_arena.investigation_models import ReceiptFilter
            from pydantic import ValidationError

            requests = (
                command.Status(),
                command.ExportPage(export_id='capture', section='receipts', limit=1),
                command.Investigate(filters=ReceiptFilter(family='writing')),
                command.Moderate(
                    command_id='reviewed-command', batch_id='reviewed-plan',
                    chunk_index=0,
                ),
            )
            for request in requests:
                restored = command.parse_request(request.model_dump_json())
                assert type(restored) is type(request)
                assert restored == request

            try:
                command.parse_request(
                    '{"operation":"investigate", "filters":{"family":"../private"}}'
                )
            except ValidationError:
                pass
            else:
                raise AssertionError('The offline parser accepted an invalid family.')

            assert commands.app
            assert not forbidden.intersection(sys.modules)
        """)

        result = subprocess.run(
            [sys.executable, '-I', '-c', script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, result.stdout + result.stderr
