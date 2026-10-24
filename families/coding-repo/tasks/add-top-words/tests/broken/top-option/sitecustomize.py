"""Leave the `--top` option out of every command-line parser."""

from verify_lib import break_option


break_option('--top', drop=True)
