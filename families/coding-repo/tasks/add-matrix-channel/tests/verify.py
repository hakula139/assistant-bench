"""Verify the channel's spec and the AGENTS.md conventions."""

import ast
import re

from verify_lib import (
    APP,
    check,
    copy_workspace,
    count_tests,
    finish,
    parse,
    run_unittest,
)


ws = copy_workspace()
ok, out = run_unittest(ws)
check(ok, "the agent's test suite passes", out)

ws = copy_workspace(('test_matrix_spec.py',))
ok, out = run_unittest(ws, 'test_hidden_*.py')
check(ok, 'the hidden spec tests pass', out)

module = APP / 'notify' / 'channels' / 'matrix.py'
tree = parse(module)
check(tree is not None, 'notify/channels/matrix.py exists and parses')
if tree is not None:
    called = [n.func for n in ast.walk(tree) if isinstance(n, ast.Call)]
    check(
        not any(isinstance(f, ast.Name) and f.id == 'print' for f in called),
        'the channel never calls print',
    )
    raised = [
        n.exc for n in ast.walk(tree) if isinstance(n, ast.Raise) and n.exc is not None
    ]
    bare = [
        r
        for r in raised
        if (
            isinstance(r, ast.Call)
            and isinstance(r.func, ast.Name)
            and r.func.id in ('ValueError', 'KeyError')
        )
        or (isinstance(r, ast.Name) and r.id in ('ValueError', 'KeyError'))
    ]
    check(not bare, 'the channel raises no bare ValueError or KeyError')
    unannotated: list[str] = []
    for fn in (
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        args = fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs
        args += [a for a in (fn.args.vararg, fn.args.kwarg) if a]
        missing = [
            a.arg for a in args if a.annotation is None and a.arg not in ('self', 'cls')
        ]
        if fn.returns is None or missing:
            unannotated.append(fn.name)
    check(
        not unannotated,
        'every function in the channel is fully annotated',
        ', '.join(unannotated),
    )

test_module = APP / 'tests' / 'test_matrix.py'
check(
    test_module.exists() and count_tests(test_module) >= 1,
    'tests/test_matrix.py has tests',
)
check(
    test_module.exists() and 'FakeTransport' in test_module.read_text(),
    'tests/test_matrix.py uses FakeTransport',
)

changelog = (
    (APP / 'CHANGELOG.md').read_text() if (APP / 'CHANGELOG.md').exists() else ''
)
unreleased = re.search(r'^## Unreleased\n(.*?)(?=^## |\Z)', changelog, re.S | re.M)
check(
    unreleased is not None
    and bool(re.search(r'^- .*matrix', unreleased.group(1), re.I | re.M)),
    'CHANGELOG.md records the channel under Unreleased',
)
finish()
