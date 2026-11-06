from types import TracebackType
from typing import Self

class threadpool_limits:
    def __init__(
        self, limits: int | None = None, user_api: str | None = None
    ) -> None: ...
    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        type: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
