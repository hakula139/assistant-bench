"""The trial labels every system harness's kwargs carry."""

from harbor.agents.options import AgentOptions


class Labels(AgentOptions):
    """The kwargs that label a trial for ingest."""

    system: str
    fingerprint: str
