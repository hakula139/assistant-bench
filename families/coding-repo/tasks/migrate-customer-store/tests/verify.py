"""Run workspace and protected behavior checks in separate scratch copies."""

from verify_lib import check, copy_workspace, finish, run_unittest


ok, out = run_unittest(copy_workspace())
check(ok, 'the workspace tests pass', out)

ok, out = run_unittest(
    copy_workspace(('test_migration_contract.py',)), 'test_hidden_*.py'
)
check(ok, 'the hidden behavior tests pass', out)
finish()
