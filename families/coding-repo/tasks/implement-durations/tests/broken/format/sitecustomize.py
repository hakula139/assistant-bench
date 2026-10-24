"""Leave `durations.format_duration` unimplemented."""

from verify_lib import after_import, replace_function


def format_duration(ms: int) -> str:
    raise NotImplementedError


after_import(
    'durations',
    lambda module: replace_function(module, 'format_duration', format_duration),
)
