import asyncio

import httpx
import openai
import pytest
from openai.types.responses import Response

from assistant_bench.harnesses.endpoint import Endpoint
from assistant_bench.harnesses.responses import ResponseResult, response_input
from support import StubEndpoint, response


class TestResponseInput:
    def test_round_trips_sdk_items_and_tool_call_ids_without_flattening(
        self, endpoint: StubEndpoint
    ) -> None:
        payload = response(None)
        payload['output'] = [
            {
                'id': 'rs-1',
                'type': 'reasoning',
                'summary': [],
                'encrypted_content': 'opaque-reasoning-content',
            },
            {
                'id': 'fc-1',
                'type': 'function_call',
                'call_id': 'call-1',
                'name': 'read_file',
                'arguments': '{"path":"greeting.txt"}',
                'status': 'completed',
            },
        ]
        sdk = Response.model_validate(payload)
        result = ResponseResult(sdk, 'req-1')
        tool_output = {
            'type': 'function_call_output',
            'call_id': 'call-1',
            'output': 'hello',
        }

        restored = response_input(
            [
                {'role': 'assistant', 'content': '', 'response_items': result.items},
                {'role': 'tool', 'content': '', 'response_items': [tool_output]},
            ],
            'Continue.',
        )

        endpoint.response = response('Continued.')

        async def replay() -> None:
            async with Endpoint.read(endpoint.env.get).client() as client:
                await client.responses.create(
                    model='acme/large', input=restored, store=False
                )

        asyncio.run(replay())

        (sent,) = endpoint.requests
        assert sent.body['input'] == [
            *payload['output'],
            tool_output,
            {'role': 'user', 'content': 'Continue.'},
        ]
        with pytest.raises(openai.APIError, match='function call'):
            result.check(
                httpx.Request('POST', 'http://127.0.0.1/v1/responses'), complete=True
            )
