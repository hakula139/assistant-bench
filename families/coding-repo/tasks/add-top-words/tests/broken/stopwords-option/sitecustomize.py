"""Accept `--stopwords FILE` in every command-line parser, but ignore the file."""

from verify_lib import break_option


break_option('--stopwords', drop=False)
