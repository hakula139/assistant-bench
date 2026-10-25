import asyncio
import json
from pathlib import Path
from uuid import uuid4

from rich.text import Text

from native_runtime_support import NativeRuntime


class TestDefaultFetch:
    def test_logs_only_safe_failure_context_and_keeps_expected_rejections_quiet(
        self, tmp_path: Path
    ) -> None:
        canary = f'synthetic-secret-canary-{uuid4()}'
        responses: list[str] = []

        async def scenario(runtime: NativeRuntime) -> None:
            async with runtime.client() as (_, transport):
                inspected = await transport.call({'operation': 'inspect'})
                assert inspected['actual_app'] is True
                assert inspected['direct_entrypoint'] is (runtime.coverage is None)
                assert await transport.call(
                    {'operation': 'fail_ingress', 'message': canary}
                ) == {'configured': True}

                unavailable = await transport.http('/api/status')
                assert unavailable['status'] == 503

                assert isinstance(unavailable['body'], str)
                assert json.loads(unavailable['body']) == {'error': 'unavailable'}
                responses.append(unavailable['body'])
                assert await transport.call({'operation': 'reject_ingress'}) == {
                    'configured': True
                }

                rejected = await transport.http('/api/status')
                assert rejected['status'] == 429

                assert isinstance(rejected['body'], str)
                assert json.loads(rejected['body']) == {'error': 'rate_limited'}
                responses.append(rejected['body'])
                for response in (unavailable, rejected):
                    headers = response['headers']
                    assert isinstance(headers, dict)
                    assert headers['content-type'] == 'application/json'
                    assert headers['cache-control'] == 'no-store'
                    assert headers['referrer-policy'] == 'no-referrer'
                    assert headers['x-content-type-options'] == 'nosniff'
                assert runtime.provider.calls == []

        with NativeRuntime(tmp_path).owner() as runtime:
            asyncio.run(scenario(runtime))

        logs = {path.name: path.read_text() for path in runtime.directory.glob('*.log')}
        prefix = 'Voting request failed:'
        events = [
            line[line.index(prefix) :]
            for line in Text.from_ansi(logs['owner.log']).plain.splitlines()
            if prefix in line
        ]
        assert events == [
            'Voting request failed: stage=preflight exception=RuntimeError.'
        ]
        assert all(canary not in body for body in responses)
        assert all(canary not in contents for contents in logs.values())
