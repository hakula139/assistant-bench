"""Verify the hidden spec tests and regression tests that fail on the old code."""

from verify_lib import (
    APP,
    check,
    copy_workspace,
    count_tests,
    fails_on,
    finish,
    run_unittest,
)


before = 4
after = count_tests(APP / 'tests' / 'test_lru.py')
check(after > before, f'tests/test_lru.py gained tests ({before} -> {after})')

ok, out = run_unittest(copy_workspace())
check(ok, "the agent's test suite passes", out)

ok, out = run_unittest(copy_workspace(('test_lru_spec.py',)), 'test_hidden_*.py')
check(ok, 'the hidden spec tests pass', out)

failed, out = fails_on('original', 'test_lru.py')
check(failed, "the agent's tests fail on the original code", '' if failed else out)
finish()
