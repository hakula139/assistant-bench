"""Official vote cuts and the pinned measurements used to score them."""

from datetime import datetime

from assistant_bench import report
from assistant_bench.arena import publication, votes
from assistant_bench.database import Database
from assistant_bench.families import Family, VotesSettings
from assistant_bench.outputs import Output, read_outputs
from assistant_bench.state import State


def capture(
    family: Family,
    private: Database,
    production: Database,
    taken_at: datetime,
    revision: str | None,
) -> votes.Snapshot | None:
    """Read the official stores at a shared cutoff and recover their collection pins."""
    assert isinstance(family.settings, VotesSettings)
    published = _pin(
        private,
        votes.read_cut(
            production,
            family.name,
            taken_at,
            include_active=family.settings.public_voting,
        ),
    )
    held = _pin(private, votes.read_cut(private, family.name, taken_at))
    if published is None and held is None:
        return None

    return votes.Snapshot(
        family=family.name,
        taken_at=taken_at,
        closed_at=taken_at,
        category_order=tuple(family.settings.categories),
        closing_revision=revision,
        method='expected_net',
        production=published,
        private=held,
    )


def _pin(database: Database, cut: votes.Cut | None) -> votes.Cut | None:
    if cut is None or not cut.inventory.outputs:
        return cut
    prepared = publication.preparation(database, cut.inventory.id)
    return cut.model_copy(
        update={'output_generation': prepared.collection_id if prepared else None}
    )


def analysis(database: Database, snapshot: votes.Snapshot) -> report.AnalysisInput:
    """Read measurements only from each cut's immutable collection generation."""
    state = State(database)
    outputs: dict[publication.VotingTarget, list[Output]] = {}
    for target, cut in snapshot.cuts():
        if cut.output_generation is not None:
            outputs[target] = read_outputs(
                snapshot.family,
                state,
                generation=state.collection_generation(cut.output_generation),
            )

    return votes.voting_input(snapshot, outputs)


def score(snapshot: votes.Snapshot, evidence: report.AnalysisInput) -> report.Report:
    """Report standings using the rules and system identity frozen with each cut."""
    return report.close_votes(
        evidence,
        snapshot.closed_at,
        fingerprints=votes.fingerprints(snapshot),
        system_names=votes.system_names(snapshot),
        net=snapshot.method == 'expected_net',
    )
