"""Accept a solution only when its visible and hidden behavior checks pass."""

from verify_lib import check, copy_workspace, finish, run_unittest


ok, output = run_unittest(copy_workspace(('test_feed_contract.py',)))
check(ok, 'The visible and hidden behavior checks pass', output)
finish()
