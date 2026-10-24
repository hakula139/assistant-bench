import pkgutil

from . import state


state.counts[__name__] = state.counts.get(__name__, 0) + 1
RESOURCE = pkgutil.get_data(__name__, 'payload.txt')


def value() -> str:
    return 'original'


def executions() -> int:
    return state.counts[__name__]


alias = value
