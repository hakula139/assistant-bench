#!/usr/bin/env bash

set -euo pipefail

if printf hello | cmp -s - /app/greeting.txt; then
  echo 1 >/logs/verifier/reward.txt
else
  echo 0 >/logs/verifier/reward.txt
fi
