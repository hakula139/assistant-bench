"""Run a public build with an operating-system limit on output file size."""

import resource
import signal
import sys
from pathlib import Path

from siteassets import BuildError, build


def main() -> int:
    source, output = Path(sys.argv[1]), Path(sys.argv[2])
    limit = int(sys.argv[3])
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))

    try:
        build(source, output)
    except BuildError, OSError:
        print('Build failed.')
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
