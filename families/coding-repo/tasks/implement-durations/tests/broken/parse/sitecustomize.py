"""Leave `durations.parse_duration` unimplemented."""

from verify_lib import after_import, replace_function


def parse_duration(text: str) -> int:
    raise NotImplementedError


after_import(
    'durations',
    lambda module: replace_function(module, 'parse_duration', parse_duration),
)
