"""Git subprocess environment isolation."""

import os
import subprocess


def git_environment() -> dict[str, str]:
    """Exclude Git's repository-local variables from the inherited environment."""
    variables = subprocess.check_output(
        ['git', 'rev-parse', '--local-env-vars'], stderr=subprocess.PIPE, text=True
    ).splitlines()
    return {name: value for name, value in os.environ.items() if name not in variables}
