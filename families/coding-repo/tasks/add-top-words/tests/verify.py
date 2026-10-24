"""Verify the hidden tests and tests that catch each broken part of the feature."""

from verify_lib import check, copy_workspace, fails_on, finish, run_unittest


ok, out = run_unittest(copy_workspace())
check(ok, "the agent's test suite passes", out)

ok, out = run_unittest(copy_workspace(('test_top_words.py',)), 'test_hidden_*.py')
check(ok, 'the hidden tests pass', out)

for variant, part in (
    ('top-words', 'top_words finds no words'),
    ('top-option', 'the CLI ignores --top'),
    ('stopwords-option', 'the CLI ignores --stopwords'),
):
    failed, out = fails_on(variant, 'test*.py')
    check(failed, f'the test suite fails when {part}', '' if failed else out)
finish()
