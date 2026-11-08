"""Prepare a canary from serialized system and job arguments."""

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from harbor.models.job.config import JobConfig

from assistant_bench import canaries
from assistant_bench.catalog import System


system = System.model_validate_json(sys.argv[1])
config = JobConfig.model_validate_json(sys.argv[2])
with TemporaryDirectory(prefix='bench-canary-protocol-') as temporary:
    (probe,) = canaries.prepare([config], {system.name: system}, root=Path(temporary))
    print(probe.protocol_digest)
