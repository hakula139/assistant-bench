import os
import ssl
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import trustme

from assistant_bench import families, jobs
from assistant_bench.arena import scheduler
from assistant_bench.arena.store import VoteStore, votes_file
from assistant_bench.catalog import EXPORT_VAR, NixosConfigExport
from assistant_bench.cli import LOCAL_SITE_VAR
from assistant_bench.harnesses.endpoint import ENDPOINT_VARS
from assistant_bench.paths import DATA_DIR_VAR
from assistant_bench.scoring import ranking
from support import EXPORT_FILE, Clock, Refits, StubEndpoint, serve_stub


# Set as `bench` sets it, before a test module imports LiteLLM.
os.environ.update(jobs.HARBOR_ENV)

PROXY_VARS = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY')


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        '--update-fixtures',
        action='store_true',
        help='Rewrite each committed fixture a test finds stale.',
    )


@pytest.fixture
def stale_fixture(request: pytest.FixtureRequest) -> Callable[[Path, str], None]:
    """Fail on a stale committed fixture, or rewrite it under `--update-fixtures`."""

    def handle(path: Path, current: str) -> None:
        __tracebackhide__ = True
        if request.config.getoption('update_fixtures'):
            path.write_text(current)
        else:
            pytest.fail(
                f'{path.name} is stale, so rerun with --update-fixtures '
                'and review the diff'
            )

    return handle


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the data directory at a scratch path, away from the shell's endpoint.

    The endpoint and proxy variables the shell exports are cleared, and the
    variable `bench serve` sets is removed after each test.
    """
    path = tmp_path / 'data'
    monkeypatch.setenv(DATA_DIR_VAR, str(path))
    # Setting it first makes teardown restore its absence.
    monkeypatch.setenv(LOCAL_SITE_VAR, '')
    monkeypatch.delenv(LOCAL_SITE_VAR)
    monkeypatch.chdir(tmp_path)
    for name in ENDPOINT_VARS:
        monkeypatch.delenv(name, raising=False)
    for name in PROXY_VARS:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    return path


@pytest.fixture
def families_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point family discovery at a scratch `families/` directory."""
    directory = tmp_path / 'families'
    monkeypatch.setattr(families, 'FAMILIES_DIR', directory)
    return directory


@pytest.fixture
def fits(monkeypatch: pytest.MonkeyPatch) -> Refits:
    """Record each refit of the scheduler's board."""
    refits = Refits()

    def rate(matchups: ranking.Matchups, root: str) -> ranking.Ratings:
        refits.comparisons.append(len(matchups.score))
        refits.roots.append(root)
        return ranking.rate(matchups, root)

    monkeypatch.setattr(scheduler, 'rate', rate)
    return refits


@pytest.fixture
def export(monkeypatch: pytest.MonkeyPatch) -> NixosConfigExport:
    monkeypatch.setenv(EXPORT_VAR, str(EXPORT_FILE))
    return NixosConfigExport.model_validate_json(EXPORT_FILE.read_bytes())


@pytest.fixture
def endpoint(tmp_path: Path) -> Iterator[StubEndpoint]:
    yield from serve_stub(tmp_path)


@pytest.fixture
def tls_endpoint(tmp_path: Path) -> Iterator[StubEndpoint]:
    """Serve the stub endpoint over TLS with a test CA's certificate for 127.0.0.1."""
    ca = trustme.CA()
    ca_file = tmp_path / 'ca.pem'
    ca.cert_pem.write_to_path(str(ca_file))
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ca.issue_cert('127.0.0.1').configure_cert(context)
    yield from serve_stub(tmp_path, context, ca_file)


@pytest.fixture
def ipv6_endpoint(tmp_path: Path) -> Iterator[StubEndpoint]:
    yield from serve_stub(tmp_path, host='::1')


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(data_dir: Path) -> Iterator[VoteStore]:
    store = VoteStore(votes_file())
    yield store
    store.close()
