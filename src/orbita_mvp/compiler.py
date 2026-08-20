from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pandas as pd

from .column_semantics import is_cluster_identifier_column, is_support_or_weight_column
from .table_domain import generate_table_candidates


class ResearchCompiler:
    """Translate a case into an explicit, reviewable, frozen analysis plan."""

    def compile(
        self,
        case: dict[str, Any],
        *,
        max_candidates: int = 60,
        policy: dict[str, Any] | None = None,
        policy_receipt: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        policy = policy or {}
        max_candidates = int(policy.get("max_candidates", max_candidates))
        scout_fraction = float(policy.get("scout_fraction", 0.6))
        seed = int(policy.get("seed", 20260623))
        files = case.get("files", [])
        tables = [f for f in files if f.get("artifact_kind") == "table" and f.get("extracted_path")]
        texts = [f for f in files if f.get("artifact_kind") == "text" and f.get("extracted_path")]
        if not tables:
            return {
                "schema_version": "orbita-research-plan/0.1",
                "mode": case.get("mode", "open_discovery"),
                "goal": case.get("goal", ""),
                "status": "needs_data",
                "blocking_questions": [
                    "No parsed tabular dataset was found. Upload CSV, Excel, JSON records, JSONL, or Parquet for automated discovery."
                ],
                "source_context": [self._source_summary(item) for item in texts],
                "routes": [],
                "candidates": [],
                "assumptions": [],
                "improvement_policy": policy_receipt,
            }
        selected = max(tables, key=lambda item: int(item.get("profile", {}).get("rows", 0)))
        df = pd.read_csv(Path(selected["extracted_path"]))
        profile = selected.get("profile", {})
        identifier_columns = [
            c["name"] for c in profile.get("column_profiles", []) if c.get("inferred_role") == "identifier"
        ]
        support_columns = sorted(
            {
                c["name"]
                for c in profile.get("column_profiles", [])
                if c.get("inferred_role") == "support_or_weight"
                or is_support_or_weight_column(str(c.get("name", "")))
            }
            | {str(column) for column in df.columns if is_support_or_weight_column(str(column))}
        )
        cluster_columns = sorted(
            {
                str(c["name"])
                for c in profile.get("column_profiles", [])
                if c.get("inferred_role") == "cluster_identifier"
            }
            | {
                str(column)
                for column in df.columns
                if is_cluster_identifier_column(str(column))
                and 1 < int(df[column].nunique(dropna=True)) < int(df[column].notna().sum())
            }
        )
        excluded_columns = sorted(set(identifier_columns) | set(support_columns) | set(cluster_columns))
        group_column = cluster_columns[0] if len(cluster_columns) == 1 else None
        cluster_error: str | None = None
        if len(cluster_columns) > 1:
            candidates = []
            cluster_error = (
                "Multiple repeated-unit identifiers were detected; choose exactly one independence cluster: "
                + ", ".join(cluster_columns)
            )
            generation = {
                "strategy": "blocked_ambiguous_cluster_identifier",
                "seed": seed,
                "scout_fraction": scout_fraction,
                "partition_unit": None,
                "group_column": None,
                "cluster_identifier_candidates": cluster_columns,
                "excluded_columns": excluded_columns,
                "generated_candidates": 0,
                "candidate_budget": max_candidates,
                "error": cluster_error,
            }
        else:
            try:
                candidates, generation = generate_table_candidates(
                    df,
                    goal=case.get("goal", ""),
                    max_candidates=max_candidates,
                    scout_fraction=scout_fraction,
                    seed=seed,
                    excluded_columns=excluded_columns,
                    group_column=group_column,
                )
            except ValueError as exc:
                if group_column is None:
                    raise
                candidates = []
                cluster_error = str(exc)
                generation = {
                    "strategy": "blocked_invalid_cluster_partition",
                    "seed": seed,
                    "scout_fraction": scout_fraction,
                    "partition_unit": "group",
                    "group_column": group_column,
                    "cluster_identifier_candidates": cluster_columns,
                    "excluded_columns": excluded_columns,
                    "generated_candidates": 0,
                    "candidate_budget": max_candidates,
                    "error": cluster_error,
                }
        assumptions = [
            {
                "id": "unit_of_analysis",
                "statement": (
                    f"Rows sharing {group_column} are treated as one dependence cluster; "
                    "scout and confirmation partitions keep clusters intact."
                    if group_column
                    else "Each row is treated as one independent unit unless the researcher says otherwise."
                ),
                "severity": "high",
                "requires_review": True,
            },
            {
                "id": "association_not_causation",
                "statement": "Automatically generated relationships are treated as associations, not causal effects.",
                "severity": "high",
                "requires_review": False,
            },
            {
                "id": "missing_values",
                "statement": "Each candidate is evaluated on rows containing the variables required by that candidate.",
                "severity": "medium",
                "requires_review": False,
            },
        ]
        quality_findings = self._quality_findings(profile)
        for column in support_columns:
            if not any(
                finding.get("title") == f"Support/count column excluded: {column}"
                for finding in quality_findings
            ):
                quality_findings.append(
                    {
                        "type": "artifact_guard",
                        "severity": "medium",
                        "title": f"Support/count column excluded: {column}",
                        "detail": (
                            "This column describes sample support or weighting and is not treated as a "
                            "scientific outcome by automatic relation mining. An explicit reviewed plan "
                            "is required to use it as an outcome."
                        ),
                    }
                )
        blocking_questions = []
        if cluster_error:
            blocking_questions.append(cluster_error)
        elif not candidates:
            blocking_questions.append(
                "No defensible automatic candidates remained after identifier and support/count/weight "
                "columns were excluded. Name an explicit scientific outcome or provide richer measurements."
            )
        return {
            "schema_version": "orbita-research-plan/0.1",
            "mode": case.get("mode", "open_discovery"),
            "goal": case.get("goal", ""),
            "status": "ready_for_review" if candidates else "no_candidates",
            "selected_dataset": {
                "file_id": selected["id"],
                "name": selected["original_name"],
                "normalized_path": selected["extracted_path"],
                "sha256": selected["sha256"],
                "rows": profile.get("rows"),
                "columns": profile.get("columns"),
            },
            "source_context": [self._source_summary(item) for item in texts],
            "data_profile": profile,
            "quality_findings": quality_findings,
            "excluded_from_candidate_generation": excluded_columns,
            "candidate_generation": generation,
            "routes": ["uploaded_table_association", "data_quality_audit", "belief_graph_import"],
            "thresholds": {
                "commit_at": float(policy.get("commit_at", 0.25)),
                "baseline_margin": float(policy.get("baseline_margin", 0.05)),
                "held_out_min": float(policy.get("held_out_min", 0.15)),
                "cross_seed_count": int(policy.get("cross_seed_count", 9)),
                "cross_seed_min": float(policy.get("cross_seed_min", 0.15)),
                "cross_seed_max_spread": policy.get("cross_seed_max_spread", 0.65),
            },
            "improvement_policy": policy_receipt,
            "candidates": candidates,
            "assumptions": assumptions,
            "blocking_questions": blocking_questions,
            "report_modules": [
                "source_inventory",
                "data_interpretation",
                "quality_and_errors",
                "surviving_findings",
                "failed_candidates",
                "assumptions",
                "limitations",
                "recommended_tests",
                "provenance_and_receipts",
            ],
        }

    def validate_external_plan(self, case: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        required = {"schema_version", "selected_dataset", "candidates", "thresholds"}
        missing = sorted(required - set(plan))
        if missing:
            raise ValueError(f"Plan is missing required fields: {', '.join(missing)}")
        plan = deepcopy(plan)
        selected = plan.get("selected_dataset")
        if not isinstance(selected, dict):
            raise ValueError("selected_dataset must be an object")
        files = case.get("files", [])
        selected_file = next(
            (item for item in files if item.get("id") == selected.get("file_id")),
            None,
        )
        if selected.get("file_id") is not None and selected_file is None:
            raise ValueError("selected_dataset.file_id does not belong to this case")
        if selected_file is None:
            dataset_hash = selected.get("sha256")
            if not isinstance(dataset_hash, str) or not dataset_hash.strip():
                raise ValueError(
                    "selected_dataset requires either a case-owned file_id or sha256 for exact case-owned resolution"
                )
            matches = [item for item in files if item.get("sha256") == dataset_hash]
            dataset_name = selected.get("name")
            if dataset_name is not None:
                matches = [
                    item
                    for item in matches
                    if item.get("original_name", item.get("name")) == dataset_name
                ]
            if len(matches) != 1:
                reason = "was not found" if not matches else "is ambiguous"
                raise ValueError(
                    f"selected_dataset sha256/name {reason} within this case; exact case-owned resolution is required"
                )
            selected_file = matches[0]
            selected["file_id"] = selected_file["id"]
        if selected.get("sha256") not in (None, selected_file.get("sha256")):
            raise ValueError("selected_dataset.sha256 does not match its case-owned file")
        expected_name = selected_file.get("original_name", selected_file.get("name"))
        if selected.get("name") not in (None, expected_name):
            raise ValueError("selected_dataset.name does not match its case-owned file")
        selected.setdefault("sha256", selected_file.get("sha256"))
        if expected_name is not None:
            selected.setdefault("name", expected_name)
        seen: set[str] = set()
        for candidate in plan.get("candidates", []):
            for field in ("id", "statement", "kind"):
                if field not in candidate:
                    raise ValueError(f"Candidate missing {field}")
            if candidate["id"] in seen:
                raise ValueError(f"Duplicate candidate id: {candidate['id']}")
            seen.add(candidate["id"])
        return plan

    def _source_summary(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "file_id": item["id"],
            "name": item["original_name"],
            "sha256": item["sha256"],
            "profile": item.get("profile", {}),
            "role": "context_only_in_v0.1",
        }

    def _quality_findings(self, profile: dict[str, Any]) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        if profile.get("duplicates", 0):
            findings.append(
                {
                    "type": "data_error",
                    "severity": "medium",
                    "title": "Duplicate rows detected",
                    "detail": f"{profile['duplicates']} exact duplicate rows were found.",
                }
            )
        for column in profile.get("column_profiles", []):
            if column.get("missing_fraction", 0) >= 0.3:
                findings.append(
                    {
                        "type": "data_quality",
                        "severity": "medium",
                        "title": f"High missingness in {column['name']}",
                        "detail": f"{column['missing_fraction']:.1%} of values are missing.",
                    }
                )
            if column.get("inferred_role") == "identifier":
                findings.append(
                    {
                        "type": "artifact_guard",
                        "severity": "low",
                        "title": f"Identifier excluded: {column['name']}",
                        "detail": "The column appears unique per row and is excluded from automatic relation mining.",
                    }
                )
            if column.get("inferred_role") == "support_or_weight":
                findings.append(
                    {
                        "type": "artifact_guard",
                        "severity": "medium",
                        "title": f"Support/count column excluded: {column['name']}",
                        "detail": (
                            "The column describes sample support or weighting and is excluded from "
                            "automatic relation mining."
                        ),
                    }
                )
            if column.get("inferred_role") == "cluster_identifier":
                findings.append(
                    {
                        "type": "artifact_guard",
                        "severity": "high",
                        "title": f"Dependence cluster preserved: {column['name']}",
                        "detail": (
                            "Rows sharing this identifier must remain together during scout and confirmation "
                            "splitting; the identifier is excluded from scientific candidate generation."
                        ),
                    }
                )
        return findings
