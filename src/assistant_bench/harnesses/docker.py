"""Docker host access for installed agents without changing task network policy."""

from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

from harbor.environments.docker.docker import DockerEnvironment
from harbor.environments.docker.ssh import local_context
from harbor.environments.ssh import SSHAccessError

from assistant_bench.messages import InputError


HOST_OVERLAY = Path(
    str(files('assistant_bench.harnesses').joinpath('docker-public.json'))
)
CONTROLLED_HOST_OVERLAY = Path(
    str(files('assistant_bench.harnesses').joinpath('docker-controlled.json'))
)


class InstalledDocker(DockerEnvironment):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        initialize = cast(Callable[..., None], super().__init__)
        initialize(*args, **kwargs)
        if (
            self._enable_egress_control
            and HOST_OVERLAY in self.extra_docker_compose_paths
        ):
            index = self.extra_docker_compose_paths.index(HOST_OVERLAY)
            self.extra_docker_compose_paths[index] = CONTROLLED_HOST_OVERLAY


async def check_local_context() -> None:
    """Require Harbor's named local Docker context before downloading any CLI."""
    try:
        await local_context()
    except SSHAccessError:
        raise InputError(
            'Installed agents require a named local Docker context. '
            'Select a local Unix-socket or Docker Desktop context with '
            '`DOCKER_CONTEXT`. A standalone `DOCKER_HOST` is unsupported, '
            'and the context cannot use an SSH or TCP endpoint.'
        ) from None
