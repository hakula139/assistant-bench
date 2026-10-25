"""Current public admission budgets shared by the owner and initial setup."""

from assistant_bench_arena.ledger_models import BootstrapConfiguration, Quota


DAY_MS = 86_400_000
ATTRIBUTION_MS = 60 * DAY_MS

QUOTAS = (
    Quota(operation='admit', scope='exact', window_ms=DAY_MS, limit=10),
    Quota(operation='admit', scope='prefix', window_ms=DAY_MS, limit=50),
    Quota(operation='admit', scope='deployment', window_ms=DAY_MS, limit=500),
    Quota(operation='renew', scope='actor', window_ms=DAY_MS, limit=10),
    Quota(operation='renew', scope='exact', window_ms=DAY_MS, limit=100),
    Quota(operation='renew', scope='prefix', window_ms=DAY_MS, limit=1_000),
    Quota(operation='renew', scope='deployment', window_ms=DAY_MS, limit=10_000),
    Quota(operation='issue', scope='actor', window_ms=DAY_MS, limit=300),
    Quota(operation='issue', scope='exact', window_ms=DAY_MS, limit=1_500),
    Quota(operation='issue', scope='prefix', window_ms=DAY_MS, limit=6_000),
    Quota(operation='issue', scope='deployment', window_ms=DAY_MS, limit=30_000),
    Quota(operation='accept', scope='actor', window_ms=DAY_MS, limit=200),
    Quota(operation='accept', scope='exact', window_ms=DAY_MS, limit=1_000),
    Quota(operation='accept', scope='prefix', window_ms=DAY_MS, limit=5_000),
    Quota(operation='accept', scope='deployment', window_ms=DAY_MS, limit=20_000),
    Quota(operation='accept', scope='actor', window_ms=60_000, limit=10),
)


def configuration(namespace: str) -> BootstrapConfiguration:
    return BootstrapConfiguration(
        namespace=namespace, quotas=QUOTAS, attribution_ms=ATTRIBUTION_MS
    )
