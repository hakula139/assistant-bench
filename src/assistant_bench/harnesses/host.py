"""Harbor environment for agents that work from the host alone."""

from pathlib import Path
from typing import NoReturn, override

from harbor.environments.base import BaseEnvironment, ExecResult
from harbor.environments.capabilities import EnvironmentCapabilities


class HostEnvironment(BaseEnvironment):
    """An environment that holds nothing, for agents that work from the host alone.

    The trial's host directories are its only files, so it reports them as mounted
    and Harbor copies no logs or artifacts. It runs no command and moves no file,
    so an agent that acts in its environment fails on its first call.
    """

    @staticmethod
    @override
    def type() -> str:
        return 'host'

    @property
    @override
    def capabilities(self) -> EnvironmentCapabilities:
        return EnvironmentCapabilities(mounted=True)

    @override
    def _validate_definition(self) -> None:
        pass

    @override
    async def start(self, force_build: bool) -> None:
        pass

    @override
    async def stop(self, delete: bool) -> None:
        pass

    @override
    async def upload_file(self, source_path: Path | str, target_path: str) -> None:
        _refuse()

    @override
    async def upload_dir(self, source_dir: Path | str, target_dir: str) -> None:
        _refuse()

    @override
    async def download_file(self, source_path: str, target_path: Path | str) -> None:
        _refuse()

    @override
    async def download_dir(self, source_dir: str, target_dir: Path | str) -> None:
        _refuse()

    @override
    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult:
        _refuse()


def _refuse() -> NoReturn:
    raise RuntimeError(
        'The host environment starts no container, so only a single-call harness '
        'runs in it.'
    )
