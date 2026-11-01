"""The trial labels every system harness's kwargs carry."""

from pydantic import BaseModel


class Labels(BaseModel):
    """The kwargs that label a trial for collection."""

    system: str
    fingerprint: str
