from __future__ import annotations

import pandas as pd

from orbita_mvp.ingestion import profile_dataframe
from orbita_mvp.table_domain import generate_table_candidates


def _sparc_like_frame() -> pd.DataFrame:
    rows = []
    for index in range(20):
        low_acceleration = index < 10
        rows.append(
            {
                "N": 18 + index * 3,
                "acceleration_regime": "low" if low_acceleration else "high",
                "log_g_bar": -12.2 + index * 0.12,
                "rar_residual_dex": 0.58 + (index % 3) * 0.01 if low_acceleration else 0.09 + (index % 3) * 0.01,
            }
        )
    return pd.DataFrame(rows)


def test_support_count_is_profiled_separately_from_measurements():
    profile = profile_dataframe(_sparc_like_frame())
    roles = {column["name"]: column["inferred_role"] for column in profile["column_profiles"]}

    assert roles["N"] == "support_or_weight"
    assert roles["rar_residual_dex"] == "measurement"


def test_sparc_like_candidate_generation_never_uses_bin_count():
    candidates, generation = generate_table_candidates(
        _sparc_like_frame(),
        goal="Does the RAR residual differ in the low acceleration regime?",
    )

    assert candidates
    assert "N" in generation["excluded_columns"]
    assert "N" not in generation["numeric_columns"]
    for candidate in candidates:
        assert candidate.get("predictor") != "N"
        assert candidate.get("outcome") != "N"
        assert candidate.get("group") != "N"
    assert any(
        candidate["kind"] == "group_difference"
        and candidate["group"] == "acceleration_regime"
        and candidate["outcome"] == "rar_residual_dex"
        for candidate in candidates
    )


def test_compiler_excludes_identifiers_and_support_counts(gateway):
    frame = _sparc_like_frame()
    frame.insert(0, "bin_id", [f"bin-{index:02d}" for index in range(len(frame))])
    case = gateway.create_case(
        name="SPARC outcome guard",
        goal="Does the RAR residual differ in the low acceleration regime?",
    )
    gateway.add_inline_file(
        case_id=case["id"],
        filename="sparc_like.csv",
        content=frame.to_csv(index=False),
    )

    record = gateway.compile_plan(case["id"])
    plan = record["plan"]

    assert set(plan["excluded_from_candidate_generation"]) == {"N", "bin_id"}
    assert plan["status"] == "ready_for_review"
    assert any(finding["title"] == "Support/count column excluded: N" for finding in plan["quality_findings"])
    assert all("N" not in {candidate.get("predictor"), candidate.get("outcome"), candidate.get("group")} for candidate in plan["candidates"])


def test_compiler_fails_closed_when_only_metadata_remains(gateway):
    frame = pd.DataFrame(
        {
            "sample_id": [f"sample-{index}" for index in range(8)],
            "N": [10 + index for index in range(8)],
        }
    )
    case = gateway.create_case(name="Metadata only", goal="Discover a scientific relationship")
    gateway.add_inline_file(
        case_id=case["id"],
        filename="metadata.csv",
        content=frame.to_csv(index=False),
    )

    plan = gateway.compile_plan(case["id"])["plan"]

    assert plan["status"] == "no_candidates"
    assert not plan["candidates"]
    assert plan["blocking_questions"]
    assert "explicit scientific outcome" in plan["blocking_questions"][0]
