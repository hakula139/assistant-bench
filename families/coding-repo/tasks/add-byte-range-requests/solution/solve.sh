#!/usr/bin/env bash

set -euo pipefail

solution_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cp -R "$solution_dir/app/." /app
