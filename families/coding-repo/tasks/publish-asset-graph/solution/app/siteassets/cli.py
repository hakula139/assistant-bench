"""Build one asset tree from the command line."""

import argparse
import json
import sys
from pathlib import Path

from .builder import build
from .models import BuildError


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()

    try:
        result = build(args.source, args.output)
    except (BuildError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1

    print(json.dumps({'directory': str(result.directory), 'manifest': result.manifest}))
    return 0
