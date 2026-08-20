"""Historical regression for the graph-program inverted-leaderboard lesson."""
from orbita_discovery.core import Falsification, Verdict, resolve_status


def test_many_empirical_survivals_cannot_outvote_one_valid_counterexample():
    verdict = Verdict(
        "supported",
        score=1.0,
        detail={
            "tested_examples": 13_000,
            "tested_graph_sizes": [4, 5, 6, 7, 8],
            "known_uncovered_regions": ["n=1", "n=2", "n=3"],
        },
    )
    attacks = [Falsification("large_graph_gauntlet", killed=False, metric=1.0)]
    attacks.append(
        Falsification(
            "small_graph_boundary_check",
            killed=True,
            metric=0.0,
            detail={"counterexample": "K2", "graph_size": 2, "coverage_bug": "testing began at n=4"},
        )
    )

    status, survived = resolve_status(verdict, attacks)

    assert status == "refuted"
    assert survived == ["large_graph_gauntlet"]
