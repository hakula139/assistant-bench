#!/usr/bin/env bash

set -uo pipefail

export PYTHONDONTWRITEBYTECODE=1
mkdir -p /logs/verifier
if python3 /tests/verify.py; then
  echo 1 >/logs/verifier/reward.txt
else
  echo 0 >/logs/verifier/reward.txt
fi
