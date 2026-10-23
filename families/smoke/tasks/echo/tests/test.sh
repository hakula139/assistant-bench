#!/usr/bin/env bash

set -euo pipefail

if [[ "$(cat /app/greeting.txt 2>/dev/null)" == "hello" ]]; then
  echo 1 >/logs/verifier/reward.txt
else
  echo 0 >/logs/verifier/reward.txt
fi
