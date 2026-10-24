"""Verify identical behavior on the probe inputs and the requested structure."""

import ast
import json
import os
import subprocess
import sys

from verify_lib import (
    APP,
    TESTS,
    TIMEOUT_SEC,
    calls,
    check,
    copy_workspace,
    finish,
    parse,
    run_unittest,
)


ws = copy_workspace()
ok, out = run_unittest(ws)
check(ok, 'the test suite passes', out)

proc = subprocess.run(
    [sys.executable, str(TESTS / 'probe.py')],
    cwd=ws,
    capture_output=True,
    text=True,
    timeout=TIMEOUT_SEC,
    env={**os.environ, 'PYTHONPATH': str(ws)},
)
expected = json.loads((TESTS / 'expected.json').read_text())
try:
    actual = json.loads(proc.stdout)
except json.JSONDecodeError:
    actual = None
if check(actual is not None, 'the probe ran', proc.stderr):
    for name, outcomes in expected.items():
        mismatches = [
            i
            for i, (want, got) in enumerate(
                zip(outcomes, actual.get(name, []), strict=False)
            )
            if want != got
        ]
        detail = '\n'.join(
            f'  input #{i}: want {outcomes[i]} got {actual[name][i]}'
            for i in mismatches[:5]
        )
        check(
            not mismatches and len(actual.get(name, [])) == len(outcomes),
            f'{name} behaves exactly as before on {len(outcomes)} probe inputs',
            detail,
        )

tree = parse(APP / 'records' / 'parsers.py')
if check(tree is not None, 'records/parsers.py parses'):
    module_defs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    check('_parse_fields' in module_defs, 'a module-level _parse_fields helper exists')
    for name in ('parse_user', 'parse_order', 'parse_product'):
        fn = module_defs.get(name)
        if not check(fn is not None, f'{name} is still a module-level function'):
            continue
        check('_parse_fields' in calls(fn), f'{name} calls _parse_fields')
        check(
            not any(isinstance(n, ast.Try) for n in ast.walk(fn)),
            f'{name} has no try statement',
        )
    helper = module_defs.get('_parse_fields')
    outside = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == 'split'
    ]
    inside = list(ast.walk(helper)) if helper else []
    check(
        all(n in inside for n in outside) and bool(outside),
        'lines are split only inside _parse_fields',
    )
finish()
