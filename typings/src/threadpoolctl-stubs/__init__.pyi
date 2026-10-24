from abc import ABC, abstractmethod
from contextlib import ContextDecorator
from ctypes import CDLL
from types import TracebackType
from typing import ClassVar, Literal, Self, final

__all__ = [
    'threadpool_limits',
    'threadpool_info',
    'ThreadpoolController',
    'LibController',
    'register',
]
__version__: str

type _Limits = (
    int
    | dict[str, int | None]
    | list[dict[str, object]]
    | ThreadpoolController
    | Literal['sequential_blas_under_openmp']
    | None
)

class LibController(ABC):
    user_api: ClassVar[str]
    internal_api: ClassVar[str]
    filename_prefixes: ClassVar[tuple[str, ...]]

    parent: ThreadpoolController | None
    prefix: str | None
    filepath: str | None
    dynlib: CDLL
    version: str | None

    @final
    def __init__(
        self,
        *,
        filepath: str | None = None,
        prefix: str | None = None,
        parent: ThreadpoolController | None = None,
    ) -> None: ...
    def info(self, debugging_info: bool = False) -> dict[str, object]: ...
    def set_additional_attributes(self) -> None: ...
    @property
    def num_threads(self) -> int | None: ...
    @abstractmethod
    def get_num_threads(self) -> int | None: ...
    @abstractmethod
    def set_num_threads(self, num_threads: int | None) -> None: ...
    @abstractmethod
    def get_version(self) -> str | None: ...

def register(controller: type[LibController]) -> None: ...
def threadpool_info(debugging_info: bool = False) -> list[dict[str, object]]: ...

class _ThreadpoolLimiter:
    def __init__(
        self,
        controller: ThreadpoolController,
        *,
        limits: _Limits = None,
        user_api: str | None = None,
    ) -> None: ...
    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        type: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
    @classmethod
    def wrap(
        cls,
        controller: ThreadpoolController,
        *,
        limits: _Limits = None,
        user_api: str | None = None,
    ) -> ContextDecorator: ...
    def restore_original_limits(self) -> None: ...
    def unregister(self) -> None: ...
    def get_original_num_threads(self) -> dict[str, int | None]: ...

class threadpool_limits:
    def __init__(self, limits: _Limits = None, user_api: str | None = None) -> None: ...
    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        type: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
    @classmethod
    def wrap(
        cls, limits: _Limits = None, user_api: str | None = None
    ) -> ContextDecorator: ...
    def restore_original_limits(self) -> None: ...
    def unregister(self) -> None: ...
    def get_original_num_threads(self) -> dict[str, int | None]: ...

class ThreadpoolController:
    lib_controllers: list[LibController]

    def __init__(self) -> None: ...
    def info(self, debugging_info: bool = False) -> list[dict[str, object]]: ...
    def select(self, **kwargs: object) -> ThreadpoolController: ...
    def limit(
        self, *, limits: _Limits = None, user_api: str | None = None
    ) -> _ThreadpoolLimiter: ...
    def wrap(
        self, *, limits: _Limits = None, user_api: str | None = None
    ) -> ContextDecorator: ...
    def __len__(self) -> int: ...
