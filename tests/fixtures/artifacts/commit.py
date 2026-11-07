import os
import subprocess
from pathlib import Path


if os.environ['TEST_ARTIFACT_HOOK'] == 'content':
    (path,) = Path('.').glob('runs/*/trials/*/job/*/agent/output.txt')
else:
    path = Path('outside.txt')
path.write_text('Changed by a synthetic commit hook.\n')
subprocess.run(['git', 'add', str(path)], check=True)
