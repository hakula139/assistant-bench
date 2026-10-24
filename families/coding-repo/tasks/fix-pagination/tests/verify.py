"""Verify that the original and hidden suites pass and the tests are untouched."""

import hashlib

from verify_lib import APP, TESTS, check, copy_workspace, finish, run_unittest


protected = {}
for line in (TESTS / 'protected.sha256').read_text().splitlines():
    digest, name = line.split(maxsplit=1)
    protected[name] = digest
    path = APP / name
    actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    check(actual == digest, f'{name} is unmodified')

added = sorted(
    path.relative_to(APP).as_posix()
    for path in (APP / 'tests').rglob('*')
    if path.is_file()
    and '__pycache__' not in path.parts
    and path.relative_to(APP).as_posix() not in protected
)
check(not added, 'tests/ holds no new files', '\n'.join(added))

ws = copy_workspace(('test_pagination_edges.py',))
ok, out = run_unittest(ws)
check(ok, 'the visible and hidden test suites pass', out)
finish()
