"""Consumed native HTTP handler interfaces from the locked LiteLLM package."""

import ssl
from collections.abc import Callable, Mapping
from typing import Any

import httpx
from aiohttp import ClientSession

class AsyncHTTPHandler:
    def __init__(
        self,
        timeout: float | httpx.Timeout | None = ...,
        event_hooks: Mapping[str, list[Callable[..., object]]] | None = ...,
        concurrent_limit: Any = ...,
        client_alias: str | None = ...,
        ssl_verify: ssl.SSLContext | bool | str | None = ...,
        shared_session: ClientSession | None = ...,
    ) -> None: ...
    @property
    def client(self) -> httpx.AsyncClient: ...
    @client.setter
    def client(self, client: httpx.AsyncClient) -> None: ...
    def create_client(
        self,
        timeout: float | httpx.Timeout | None,
        event_hooks: Mapping[str, list[Callable[..., object]]] | None,
        ssl_verify: ssl.SSLContext | bool | str | None = ...,
        shared_session: ClientSession | None = ...,
    ) -> httpx.AsyncClient: ...
    async def close(self) -> None: ...
