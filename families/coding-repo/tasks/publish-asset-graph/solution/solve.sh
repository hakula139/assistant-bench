#!/usr/bin/env bash

set -euo pipefail

solution_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${BENCH_APP_DIR:-/app}"
cp -R "$solution_dir/app/." .
