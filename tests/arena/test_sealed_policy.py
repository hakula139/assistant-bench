import math

from assistant_bench_arena.serialization import digest
from assistant_bench_arena.snapshots import (
    AnalysisPolicy,
    CappedHumanWeights,
    SettledWeights,
    UnavailableInference,
    VotesMethod,
    VotesRules,
)


class TestSealedPolicy:
    def test_keeps_the_policy_the_production_ledger_sealed(self) -> None:
        """The live Worker parses this sealed policy on every session start."""
        policy = AnalysisPolicy(
            method=VotesMethod(
                name='bradley-terry',
                version=1,
                l2_penalty=0.01,
                points_per_logit=400 / math.log(10),
                anchor_score=1000.0,
                human_weights=CappedHumanWeights(
                    name='actor-proportional-cap',
                    version=1,
                    cap=100,
                    scope='family-evaluation',
                    repeat_handling='excluded',
                ),
                settled_weights=SettledWeights(name='sample-pair-share', version=1),
            ),
            rules=VotesRules(
                preliminary_decisive_votes=40,
                preliminary_decisive_vote_unit='raw-votes',
                rater_agreement_floor=0.7,
            ),
            inference=UnavailableInference(
                status='unavailable',
                target='new-cases-and-voters',
                reason='population-method-not-validated',
            ),
        )

        sealed = digest(policy)

        assert sealed == (
            '50868d7f116faa7aeec5e06e04ac92fcd43a431f741d3d436276628355656a47'
        )
