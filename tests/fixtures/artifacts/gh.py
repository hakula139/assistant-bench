import os
import sys
from pathlib import Path


if sys.argv[1:3] != ['api', 'repos/example/private-data']:
    raise SystemExit(2)

sys.stdout.write(Path(os.environ['TEST_GH_METADATA']).read_text())
raise SystemExit(int(os.environ.get('TEST_GH_STATUS', '0')))
