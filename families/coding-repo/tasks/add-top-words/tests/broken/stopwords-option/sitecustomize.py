"""Accept `--stopwords FILE` in every command-line parser, but ignore the file."""

from verify_lib import ignore_option


ignore_option('--stopwords')
