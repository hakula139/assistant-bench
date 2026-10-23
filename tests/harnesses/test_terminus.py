import asyncio
from importlib import metadata
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from harbor.agents.terminus_2.terminus_2 import Terminus2
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from assistant_bench.catalog import NixosConfigExport
from assistant_bench.families import TaskTestsSettings
from assistant_bench.harnesses import options
from assistant_bench.harnesses.terminus import Terminus
from support import StubEndpoint, make_system


SETTINGS = TaskTestsSettings(
    scorer='tests', display_name='Family', samples=1, max_output_tokens=64
)

pytestmark = pytest.mark.usefixtures('export')


class TestTerminus:
    class TestPreflight:
        OPTIONS = {'system': 's', 'fingerprint': 'f'}

        def test_rejects_an_effort_the_agent_does_not_accept(
            self, endpoint: StubEndpoint
        ) -> None:
            with pytest.raises(ValueError, match='reasoning_effort'):
                Terminus.preflight(
                    self.OPTIONS | {'reasoning_effort': 'turbo'}, endpoint.env
                )

    class TestRun:
        def test_keeps_its_metadata_when_the_parent_raises(
            self,
            endpoint: StubEndpoint,
            export: NixosConfigExport,
            monkeypatch: pytest.MonkeyPatch,
            tmp_path: Path,
        ) -> None:
            agent = Terminus(
                logs_dir=tmp_path,
                model_name='openai/acme/large',
                extra_env=endpoint.env,
                **options.terminus(make_system(harness='terminus-2'), SETTINGS),
            )
            context = AgentContext()

            async def fail(
                self: Terminus2,
                instruction: str,
                environment: BaseEnvironment,
                context: AgentContext,
            ) -> None:
                context.metadata = {'n_episodes': 1}
                raise RuntimeError('agent failed')

            monkeypatch.setattr(Terminus2, 'run', fail)

            with pytest.raises(RuntimeError, match='agent failed'):
                asyncio.run(agent.run('Go.', MagicMock(spec=BaseEnvironment), context))

            assert context.metadata == {
                'n_episodes': 1,
                'nixos_config_rev': export.rev,
                'harbor_version': metadata.version('harbor'),
            }
