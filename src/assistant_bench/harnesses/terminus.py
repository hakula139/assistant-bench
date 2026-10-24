"""Harbor's terminus-2 agent, sending its model calls to the bench endpoint."""

from collections.abc import Mapping
from importlib import metadata
from typing import Any, ClassVar, override

from harbor.agents.terminus_2.terminus_2 import Terminus2, Terminus2Options
from harbor.environments.base import BaseEnvironment
from harbor.llms.base import BaseLLM
from harbor.models.agent.context import AgentContext

from assistant_bench.catalog import load_export
from assistant_bench.harnesses.endpoint import Endpoint, preflight
from assistant_bench.harnesses.labels import Labels


class TerminusOptions(Labels, Terminus2Options):
    pass


class Terminus(Terminus2):
    """Harbor's `Terminus2`, whose LiteLLM client comes from the bench's endpoint.

    The client carries the key file, the CA bundle, the extra headers, and the
    local address, so none of them enters the job's `kwargs`, `env`, or config.
    """

    options_model: ClassVar[type[Terminus2Options]] = TerminusOptions

    @classmethod
    @override
    def preflight(
        cls,
        kwargs: dict[str, Any] | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        super().preflight(kwargs, env)
        preflight(env)

    @override
    def _init_llm(self, *args: Any, **kwargs: Any) -> BaseLLM:
        endpoint = Endpoint.read(self._get_env)
        kwargs['llm_kwargs'] = {
            **(kwargs.get('llm_kwargs') or {}),
            'client': endpoint.client(),
        }
        return super()._init_llm(*args, **kwargs)

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        nixos_config_rev = load_export(self._get_env).rev
        harbor_version = metadata.version('harbor')
        try:
            await super().run(instruction, environment, context)
        finally:
            context.metadata = {
                **(context.metadata or {}),
                'nixos_config_rev': nixos_config_rev,
                'harbor_version': harbor_version,
            }
