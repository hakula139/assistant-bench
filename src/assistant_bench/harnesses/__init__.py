"""Harness names, each mapped to the Harbor agent that runs it.

A bench harness maps to its import path, and a harness Harbor ships maps to its
agent name. Harbor records the agent of every trial in the job's `config.json`,
where `harbor job resume` reads it back, so an import path never changes.
"""

from typing import Any


HARNESSES: dict[str, dict[str, Any]] = {
    'generate': {'import_path': 'assistant_bench.harnesses.generate:Generate'},
    'oracle': {'name': 'oracle'},
}
