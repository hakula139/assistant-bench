"""Verify the hidden spec tests and tests that catch each unimplemented function."""

from verify_lib import check, copy_workspace, fails_on, finish, run_unittest


ok, out = run_unittest(copy_workspace())
check(ok, "the agent's test suite passes", out)

ok, out = run_unittest(copy_workspace(('test_durations_spec.py',)), 'test_hidden_*.py')
check(ok, 'the hidden spec tests pass', out)

for variant, function in (('parse', 'parse_duration'), ('format', 'format_duration')):
    failed, out = fails_on(variant, 'test_durations.py')
    check(
        failed,
        f'tests/test_durations.py fails while {function} is unimplemented',
        '' if failed else out,
    )
finish()
