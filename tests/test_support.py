import asyncio

import httpx
import pytest


class TestNetworkGuard:
    @pytest.mark.parametrize(
        ('method', 'url'),
        [
            ('POST', 'https://example.com/release'),
            ('GET', 'https://example.com/unregistered'),
            ('GET', 'http://example.com/release'),
        ],
    )
    def test_registered_downloads_never_permit_model_posts_or_other_paths(
        self,
        loopback_requests_only: set[tuple[str, str]],
        method: str,
        url: str,
    ) -> None:
        loopback_requests_only.add(('example.com', '/release'))

        with (
            httpx.Client() as client,
            pytest.raises(pytest.fail.Exception, match='non-loopback'),
        ):
            client.request(method, url)

    def test_registered_downloads_never_permit_async_model_transport(
        self, loopback_requests_only: set[tuple[str, str]]
    ) -> None:
        loopback_requests_only.add(('example.com', '/release'))

        async def request() -> None:
            async with httpx.AsyncClient() as client:
                with pytest.raises(pytest.fail.Exception, match='non-loopback'):
                    await client.get('https://example.com/release')

        asyncio.run(request())
