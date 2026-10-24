"""Run in a fresh process because registering controllers mutates global state."""

from typing import ClassVar

from threadpoolctl import LibController, register, threadpool_limits


class CustomController(LibController):
    user_api: ClassVar[str] = 'contract_check_api'
    internal_api: ClassVar[str] = 'contract_check_backend'
    filename_prefixes: ClassVar[tuple[str, ...]] = ('lib_contract_check',)

    def get_num_threads(self) -> int:
        return 1

    def set_num_threads(self, num_threads: int | None) -> None:
        pass

    def get_version(self) -> str:
        return '1'


def check_controller(controller: type[LibController]) -> None:
    assert controller.internal_api
    assert controller.filename_prefixes
    register(controller)

    with threadpool_limits(limits=1, user_api=controller.user_api) as limits:
        assert limits.get_original_num_threads() == {controller.user_api: None}


check_controller(CustomController)
