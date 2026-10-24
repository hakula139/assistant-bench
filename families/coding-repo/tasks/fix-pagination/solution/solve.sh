#!/usr/bin/env bash

set -euo pipefail

cd /app
python3 - <<'PY'
import pathlib

path = pathlib.Path("ledger/pagination.py")
text = path.read_text()
text = text.replace("return self.number <= self.total_pages", "return self.number < self.total_pages")
text = text.replace("return max(1, count // per_page)", "return max(1, -(-count // per_page))")
text = text.replace("start = number * per_page", "start = (number - 1) * per_page")
path.write_text(text)
PY
