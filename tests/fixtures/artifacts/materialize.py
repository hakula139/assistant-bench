import sys
from pathlib import Path

from assistant_bench.artifacts import ArtifactRef, ArtifactRepository


repository = ArtifactRepository(sys.argv[1])
destination = Path(sys.argv[2])
repository.materialize_trials(
    [
        (ArtifactRef.model_validate_json(reference), destination / str(index))
        for index, reference in enumerate(sys.argv[3:])
    ]
)
