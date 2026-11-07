import os
import sys
import time
from pathlib import Path


barrier = Path(os.environ['TEST_PUSH_BARRIER'])
_, revision, _ = sys.stdin.readline().split()
(barrier / revision).touch()
deadline = time.monotonic() + 15
while len(list(barrier.iterdir())) < 2:
    if time.monotonic() >= deadline:
        raise SystemExit(1)
    time.sleep(0.01)
