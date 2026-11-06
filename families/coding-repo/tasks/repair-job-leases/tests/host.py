"""Run one scenario against the workspace, writing its observation if asked.

Run as `python3 -I host.py <module> <function> [<fd>]` from the workspace copy,
with the scenario's keyword arguments as a JSON object on the first line of
stdin, which leaves the rest of stdin to the scenario. The result of
`scenarios.<module>.<function>`, awaited when it is a coroutine, goes to the
inherited file descriptor `fd` as JSON once the scenario returns.
"""

import importlib
import json
import os
import sys
import types
from typing import Any, cast


ROOT = os.path.dirname(os.path.realpath(__file__))


def main() -> None:
    module, function, *fd = sys.argv[1:]
    args = json.loads(sys.stdin.readline())
    sys.path[:0] = [ROOT, os.path.join(ROOT, 'ws')]

    scenario = getattr(importlib.import_module(f'scenarios.{module}'), function)
    observation: Any = scenario(**args)
    if isinstance(observation, types.CoroutineType):
        # Most scenarios are synchronous, and importing asyncio would slow
        # every one of their start-ups.
        import asyncio

        observation = asyncio.run(cast(types.CoroutineType[Any, Any, Any], observation))

    if fd:
        with os.fdopen(int(fd[0]), 'w') as record:
            json.dump(observation, record)


if __name__ == '__main__':
    main()
