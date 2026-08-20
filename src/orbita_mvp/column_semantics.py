from __future__ import annotations

import re

_SUPPORT_OR_WEIGHT_NAMES = {
    "n",
    "count",
    "counts",
    "freq",
    "frequency",
    "frequencies",
    "sample_count",
    "sample_size",
    "n_obs",
    "n_observations",
    "num_observations",
    "number_of_observations",
    "bin_count",
    "support",
    "weight",
    "weights",
}

_CLUSTER_IDENTIFIER_NAMES = {
    "galaxy",
    "galaxy_id",
    "galaxy_name",
    "subject",
    "subject_id",
    "patient",
    "patient_id",
    "participant",
    "participant_id",
    "household",
    "household_id",
    "site",
    "site_id",
    "device",
    "device_id",
    "specimen",
    "specimen_id",
    "sample_id",
}


def normalized_column_name(name: str) -> str:
    """Return a stable semantic form without interpreting the column values."""

    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


def is_support_or_weight_column(name: str) -> bool:
    """Recognize columns that usually describe evidence support, not an outcome.

    Automatic discovery excludes this deliberately narrow list. A researcher may
    still submit an explicit reviewed plan when a count is genuinely the outcome.
    """

    normalized = normalized_column_name(name)
    if normalized in _SUPPORT_OR_WEIGHT_NAMES:
        return True
    return normalized.startswith(("n_samples", "n_subjects", "n_patients", "n_galaxies")) or normalized.endswith(
        ("_sample_count", "_observation_count", "_bin_count")
    )


def is_cluster_identifier_column(name: str) -> bool:
    """Recognize columns that commonly identify repeated dependence units.

    Values still have to repeat before ingestion assigns ``cluster_identifier``.
    This deliberately narrow name guard avoids guessing from arbitrary categorical
    columns such as treatment arms or acceleration regimes.
    """

    return normalized_column_name(name) in _CLUSTER_IDENTIFIER_NAMES
