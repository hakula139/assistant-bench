"""Verify the hidden spec tests and a regression test for each reported bug."""

from verify_lib import check, copy_workspace, fails_on, finish, run_unittest


ok, out = run_unittest(copy_workspace())
check(ok, "the agent's test suite passes", out)

ok, out = run_unittest(copy_workspace(('test_lru_spec.py',)), 'test_hidden_*.py')
check(ok, 'the hidden spec tests pass', out)

for variant, bug in (
    ('falsy-values', 'get treats a falsy value as missing'),
    ('replace-when-full', 'put of a cached key evicts another when full'),
):
    failed, out = fails_on(variant, 'test_lru.py')
    check(failed, f'tests/test_lru.py fails when {bug}', '' if failed else out)
finish()
