#!/usr/bin/env bash

set -euo pipefail

cd /app
python3 - <<'PY'
import pathlib
import re

for path in list(pathlib.Path("shop").glob("*.py")) + list(pathlib.Path("tests").glob("*.py")):
    text = path.read_text()
    text = text.replace("get_customer(", "find_customer(").replace("import get_customer", "import find_customer")
    text = text.replace("handle_find_customer", "handle_get_customer")
    text = re.sub(r"\bcustomer\.name\b", "customer.display_name", text)
    text = text.replace("get_customer(3).name", "get_customer(3).display_name")
    text = text.replace("find_customer(3).name", "find_customer(3).display_name")
    text = text.replace("name=\"Grace Hopper\"", "display_name=\"Grace Hopper\"")
    text = text.replace("    name: str\n", "    display_name: str\n")
    path.write_text(text)

repo = pathlib.Path("shop/repository.py")
text = repo.read_text()
text = text.replace('"""In-memory storage."""\n\n', '"""In-memory storage."""\n\nimport warnings\n\n')
text += '''

def get_customer(customer_id: int) -> Customer:
    """Deprecated alias of ``find_customer``."""
    warnings.warn("get_customer is deprecated, use find_customer", DeprecationWarning, stacklevel=2)
    return find_customer(customer_id)
'''
repo.write_text(text)
PY
python3 -m unittest -q
git add -A
git commit -q -m "refactor: rename get_customer to find_customer and Customer.name to display_name"
