"""Verify the renamed API, the alias, no stale callers, and one refactor commit."""

import ast
import re

from verify_lib import APP, check, copy_workspace, finish, git, parse, run_unittest


ws = copy_workspace()
ok, out = run_unittest(ws)
check(ok, "the agent's test suite passes", out)

ws = copy_workspace(('test_renamed_api.py',))
ok, out = run_unittest(ws, 'test_hidden_*.py')
check(ok, 'the hidden tests pass', out)

stale: list[str] = []
for path in sorted((APP / 'shop').glob('*.py')) + sorted((APP / 'tests').glob('*.py')):
    tree = parse(path)
    if tree is None:
        stale.append(f'{path.name}: does not parse')
        continue
    rel = path.relative_to(APP).as_posix()
    for node in ast.walk(tree):
        if (
            rel.startswith('shop/')
            and isinstance(node, ast.Attribute)
            and node.attr == 'name'
            and 'customer' in ast.unparse(node.value).lower()
        ):
            stale.append(f'{rel}:{node.lineno} uses .name on a customer')
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == 'Customer'
            and any(k.arg == 'name' for k in node.keywords)
        ):
            stale.append(f'{rel}:{node.lineno} passes name= to Customer')
        if (
            rel.startswith('shop/')
            and rel != 'shop/repository.py'
            and (
                (isinstance(node, ast.Name) and node.id == 'get_customer')
                or (isinstance(node, ast.alias) and node.name == 'get_customer')
            )
        ):
            stale.append(
                f'{rel}:{getattr(node, "lineno", "?")} still uses get_customer'
            )
check(not stale, 'no caller still uses the old names', '\n'.join(stale))

subjects = git('log', '--format=%s').splitlines()
check(len(subjects) == 2, 'exactly one commit was added', '\n'.join(subjects))
check(
    bool(subjects)
    and re.match(r'^refactor(\([^)]+\))?!?: \S', subjects[0]) is not None,
    'the commit subject uses the refactor type',
    subjects[0] if subjects else '',
)
check(
    git('status', '--porcelain') == '',
    'the working tree is clean',
    git('status', '--porcelain'),
)
finish()
