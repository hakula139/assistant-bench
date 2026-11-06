#!/usr/bin/env bash

set -euo pipefail

export PYTHONDONTWRITEBYTECODE=1
mkdir -p /logs/verifier
if python3 /tests/verify.py; then
  reward=1
else
  reward=0
fi
# The tested code ran as a user that Harbor's world-writable log mount lets
# write here, while Docker Desktop's mounts ignore modes, so drop whatever it
# left where Harbor reads a reward. Harbor reads reward.json first.
rm -rf /logs/verifier/reward.json /logs/verifier/reward.txt
echo "$reward" >/logs/verifier/reward.txt
