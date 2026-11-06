#!/usr/bin/env bash

set -euo pipefail

# Command substitution strips trailing newlines, so the sentinel keeps them.
greeting="$(cat /app/greeting.txt 2>/dev/null && printf x)" || greeting=''
if [[ "$greeting" == "hellox" ]]; then
  echo 1 >/logs/verifier/reward.txt
else
  echo 0 >/logs/verifier/reward.txt
fi
