"""Prepare a canary from serialized system and job arguments."""

import sys

from harbor.models.job.config import JobConfig

from assistant_bench import canaries
from assistant_bench.catalog import System


system = System.model_validate_json(sys.argv[1])
config = JobConfig.model_validate_json(sys.argv[2])
print(canaries.prepare([config], {system.name: system})[0].protocol_digest)
