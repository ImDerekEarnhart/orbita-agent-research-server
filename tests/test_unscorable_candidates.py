"""A candidate nothing could measure must not be recorded as one the evidence refuted.

This is a regression test for run_c6b010982fd04cf0 ("Operation Golden Gate"), where six
prose strategy statements were fed to the numeric falsifier suite. The table domain did
not know how to fit them, returned a zero score, and all three falsifiers therefore
killed every candidate at exactly 0.000 with verdict status "unknown". The run was then
imported into the belief graph as six `falsified_candidate` claims — refutations resting
on evidence that was never gathered.
"""

from __future__ import annotations

import pytest

from orbita_discovery.core import (
    Candidate,
    CandidateNotScorable,
    Falsification,
    Verdict,
    resolve_status,
)
from orbita_discovery.falsifiers import BaselineFalsifier, CrossSeedFalsifier, HeldOutFalsifier


class _ProseDomain:
    """A fittable domain that honestly refuses candidates it cannot measure."""

    name = "prose"

    def splits(self, evidence, seed):
        return evidence, evidence

    def refit(self, candidate, train):
        raise CandidateNotScorable(
            f"this domain cannot fit a candidate of kind {candidate.payload.get('kind')!r}"
        )

    def score(self, candidate, model, test):  # pragma: no cover - never reached
        raise AssertionError("score must not be called once refit has refused")

    def baseline_score(self, test):
        return 0.0


PROSE = Candidate(
    id="H1",
    statement="Fly one-way is the best immediate travel route",
    payload={"kind": "strategic_hypothesis"},
)


@pytest.mark.parametrize(
    "falsifier", [BaselineFalsifier(), HeldOutFalsifier(), CrossSeedFalsifier()]
)
def test_no_falsifier_kills_what_it_could_not_measure(falsifier):
    result = falsifier.attempt(PROSE, [1, 2, 3, 4], _ProseDomain())
    assert result.killed is False
    assert result.detail["unscorable"]


def test_an_unmeasurable_candidate_is_unscorable_not_refuted():
    attacks = [
        Falsification(name, False, detail={"unscorable": "cannot fit"})
        for name in ("baseline", "held_out", "cross_seed")
    ]
    status, survived = resolve_status(Verdict("unknown", 0.0), attacks)

    assert status == "unscorable"
    # It must not be credited with surviving the attacks either.
    assert survived == []


def test_a_genuine_refutation_is_still_a_refutation():
    attacks = [
        Falsification("baseline", True, metric=-0.4, detail={"score": -0.4}),
        Falsification("held_out", False, metric=0.8, detail={"score": 0.8}),
        Falsification("cross_seed", False, metric=0.7, detail={"median": 0.7}),
    ]
    status, survived = resolve_status(Verdict("provisional", 0.8), attacks)

    assert status == "refuted"
    assert survived == ["held_out", "cross_seed"]


def test_a_real_survivor_is_unaffected():
    attacks = [
        Falsification("baseline", False, metric=0.6, detail={"score": 0.9}),
        Falsification("held_out", False, metric=0.85, detail={"score": 0.85}),
        Falsification("cross_seed", False, metric=0.82, detail={"median": 0.82}),
    ]
    status, survived = resolve_status(Verdict("supported", 0.85), attacks)

    assert status == "supported"
    assert survived == ["baseline", "held_out", "cross_seed"]


def test_the_real_table_domain_refuses_an_unknown_candidate_kind():
    """The production domain must raise rather than score prose as zero."""
    import pandas as pd

    from orbita_mvp.table_domain import UploadedTableDomain

    frame = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0], "y": [2.0, 4.0, 6.0, 8.0, 10.0]})
    domain = UploadedTableDomain.__new__(UploadedTableDomain)

    with pytest.raises(CandidateNotScorable, match="strategic_hypothesis"):
        domain.refit(PROSE, frame)


def test_the_real_table_domain_refuses_to_score_an_invalid_model():
    import pandas as pd

    from orbita_mvp.table_domain import UploadedTableDomain

    frame = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0], "y": [2.0, 4.0, 6.0, 8.0, 10.0]})
    domain = UploadedTableDomain.__new__(UploadedTableDomain)

    with pytest.raises(CandidateNotScorable):
        domain.score(PROSE, {"kind": "strategic_hypothesis", "valid": False}, frame)


def test_a_real_linear_candidate_still_scores_normally():
    """The guard must not make genuine tabular work unscorable."""
    import pandas as pd

    from orbita_mvp.table_domain import UploadedTableDomain

    frame = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0], "y": [2.0, 4.0, 6.0, 8.0, 10.0]})
    domain = UploadedTableDomain.__new__(UploadedTableDomain)
    candidate = Candidate(
        id="L1",
        statement="y tracks x",
        payload={"kind": "linear_association", "predictor": "x", "outcome": "y"},
    )

    model = domain.refit(candidate, frame)
    assert model["valid"] is True
    assert domain.score(candidate, model, frame) == pytest.approx(1.0, abs=1e-6)
