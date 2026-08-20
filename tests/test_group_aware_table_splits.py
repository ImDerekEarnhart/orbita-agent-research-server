from __future__ import annotations

import pandas as pd
import pytest

from orbita_mvp.ingestion import profile_dataframe
from orbita_mvp.table_domain import UploadedTableDomain, generate_table_candidates


def _galaxy_frame(galaxies: int = 12, rows_per_galaxy: int = 3) -> pd.DataFrame:
    rows = []
    for galaxy_index in range(galaxies):
        regime = "low" if galaxy_index < galaxies // 2 else "high"
        for radius_index in range(rows_per_galaxy):
            rows.append(
                {
                    "galaxy_id": f"G-{galaxy_index:02d}",
                    "acceleration_regime": regime,
                    "log_g_bar": -12.0 + galaxy_index * 0.15 + radius_index * 0.02,
                    "rar_residual": (0.55 if regime == "low" else 0.08) + radius_index * 0.01,
                    "N": 20 + galaxy_index,
                }
            )
    return pd.DataFrame(rows)


def test_repeated_galaxy_identifier_gets_cluster_role():
    profile = profile_dataframe(_galaxy_frame())
    roles = {column["name"]: column["inferred_role"] for column in profile["column_profiles"]}

    assert roles["galaxy_id"] == "cluster_identifier"
    assert roles["N"] == "support_or_weight"


def test_candidate_generation_partitions_whole_galaxies_and_excludes_identifier():
    frame = _galaxy_frame()
    candidates, generation = generate_table_candidates(
        frame,
        goal="Does rar_residual differ by acceleration_regime?",
        excluded_columns=["N", "galaxy_id"],
        group_column="galaxy_id",
    )

    assert candidates
    assert generation["partition_unit"] == "group"
    assert generation["group_column"] == "galaxy_id"
    assert generation["group_overlap_count"] == 0
    assert generation["scout_group_count"] + generation["confirmation_group_count"] == 12
    assert "galaxy_id" in generation["excluded_columns"]
    assert all(
        "galaxy_id" not in {candidate.get("predictor"), candidate.get("outcome"), candidate.get("group")}
        for candidate in candidates
    )


def test_execution_and_bootstraps_keep_confirmation_galaxies_out_of_scout():
    frame = _galaxy_frame()
    candidates, _ = generate_table_candidates(
        frame,
        excluded_columns=["N", "galaxy_id"],
        group_column="galaxy_id",
    )
    domain = UploadedTableDomain(frame, candidates, group_column="galaxy_id")

    scout_groups = set(domain.scout["galaxy_id"])
    confirmation_groups = set(domain.confirmation["galaxy_id"])
    assert scout_groups.isdisjoint(confirmation_groups)

    train, resampled = domain.splits(domain.evidence_for(next(domain.propose())), seed=7)
    assert set(train["galaxy_id"]).isdisjoint(set(resampled["galaxy_id"]))
    counts = resampled.groupby("galaxy_id").size()
    assert all(count % 3 == 0 for count in counts)


def test_compiler_records_group_safe_partition(gateway):
    frame = _galaxy_frame()
    case = gateway.create_case(name="Galaxy-aware SPARC", goal="Test the residual pattern")
    gateway.add_inline_file(case_id=case["id"], filename="sparc.csv", content=frame.to_csv(index=False))

    plan = gateway.compile_plan(case["id"])["plan"]

    assert plan["status"] == "ready_for_review"
    assert plan["candidate_generation"]["partition_unit"] == "group"
    assert plan["candidate_generation"]["group_column"] == "galaxy_id"
    assert plan["candidate_generation"]["group_overlap_count"] == 0
    assert {"galaxy_id", "N"}.issubset(plan["excluded_from_candidate_generation"])
    assert any("dependence cluster" in item["statement"] for item in plan["assumptions"])


def test_compiler_fails_closed_when_cluster_identifier_is_ambiguous(gateway):
    frame = _galaxy_frame()
    frame["site_id"] = [f"site-{index % 3}" for index in range(len(frame))]
    case = gateway.create_case(name="Ambiguous clusters", goal="Test the residual pattern")
    gateway.add_inline_file(case_id=case["id"], filename="ambiguous.csv", content=frame.to_csv(index=False))

    plan = gateway.compile_plan(case["id"])["plan"]

    assert plan["status"] == "no_candidates"
    assert not plan["candidates"]
    assert plan["candidate_generation"]["strategy"] == "blocked_ambiguous_cluster_identifier"
    assert set(plan["candidate_generation"]["cluster_identifier_candidates"]) == {"galaxy_id", "site_id"}
    assert "choose exactly one independence cluster" in plan["blocking_questions"][0]


def test_group_split_requires_two_groups_on_each_side():
    frame = _galaxy_frame(galaxies=3)

    with pytest.raises(ValueError, match="fewer than 4 groups"):
        generate_table_candidates(frame, group_column="galaxy_id")
