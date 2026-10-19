"""Leakage/labeling contract for the WATCH feature table (Giai doan A, step 3
in docs/ML_PERSONALIZED_WATCH_SYSTEM_PLAN.md).

Run directly: pytest data/ml/personalized_watch/test_no_leakage.py
Requires build_watch_feature_table.py to have already been run — see
docs/ML_PERSONALIZED_WATCH_SYSTEM_PLAN.md section 6 for the command.
"""

import csv
from pathlib import Path

import pytest

OUTPUT_DIR = Path(__file__).parent
FEATURE_TABLE = OUTPUT_DIR / "feature_table.csv"
FEATURE_DICTIONARY = OUTPUT_DIR / "feature_dictionary.csv"

# Columns the rule engine derives *from* the label (or that only exist once a
# WATCH decision has already been made) — see
# docs/ML_HANDOFF_PERSONALIZED_ATTENTION.md section 6.2. None of these may
# ever appear as a column in the built feature table, not even for audit.
FORBIDDEN_COLUMNS = {
    "attention_score",
    "primary_reason_code",
    "risk_modifiers",
    "evidence_snapshot",
    "suggestion_code",
    "suggestion_text",
    "suggestion_status",
    "event_state",
}

# status/eligible_for_binary_training/y_watch are allowed to exist (a
# training script needs the label somewhere), but only ever as source="label"
# in the dictionary — never counted as a feature a model is allowed to train on.
LABEL_COLUMNS = {"status", "eligible_for_binary_training", "y_watch"}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def feature_rows() -> list[dict[str, str]]:
    if not FEATURE_TABLE.exists():
        pytest.skip(f"{FEATURE_TABLE} not built yet — run build_watch_feature_table.py first")
    return _read_csv(FEATURE_TABLE)


@pytest.fixture(scope="module")
def dictionary_rows() -> list[dict[str, str]]:
    if not FEATURE_DICTIONARY.exists():
        pytest.skip(f"{FEATURE_DICTIONARY} not built yet — run build_watch_feature_table.py first")
    return _read_csv(FEATURE_DICTIONARY)


def test_no_forbidden_columns_in_feature_table(feature_rows: list[dict[str, str]]) -> None:
    columns = set(feature_rows[0].keys())
    leaked = columns & FORBIDDEN_COLUMNS
    assert not leaked, f"label-derived columns leaked into the feature table: {leaked}"


def test_label_columns_are_tagged_as_label_source(dictionary_rows: list[dict[str, str]]) -> None:
    by_name = {row["feature_name"]: row["source"] for row in dictionary_rows}
    for name in LABEL_COLUMNS:
        assert by_name.get(name) == "label", (
            f"{name} must be tagged source='label' in feature_dictionary.csv so a "
            "training script that selects time_series_derived/static_clinical "
            "columns as X can never pick it up"
        )


def test_no_forbidden_column_is_tagged_as_a_feature(dictionary_rows: list[dict[str, str]]) -> None:
    feature_sources = {"time_series_derived", "static_clinical"}
    leaked = {
        row["feature_name"]
        for row in dictionary_rows
        if row["feature_name"] in FORBIDDEN_COLUMNS and row["source"] in feature_sources
    }
    assert not leaked, f"forbidden columns tagged as usable features: {leaked}"


def test_no_baseline_learning_rows_emitted(feature_rows: list[dict[str, str]]) -> None:
    statuses = {row["status"] for row in feature_rows}
    assert "BASELINE_LEARNING" not in statuses, (
        "BASELINE_LEARNING rows carry no WATCH/NORMAL label and must never be emitted"
    )


def test_y_watch_matches_status(feature_rows: list[dict[str, str]]) -> None:
    for row in feature_rows:
        if row["status"] == "WATCH":
            assert row["y_watch"] == "1", row
            assert row["eligible_for_binary_training"] == "True", row
        elif row["status"] == "NORMAL":
            assert row["y_watch"] == "0", row
            assert row["eligible_for_binary_training"] == "True", row
        else:  # DANGER
            assert row["y_watch"] == "", row
            assert row["eligible_for_binary_training"] == "False", row


def test_row_count_matches_watch_normal_danger_only(feature_rows: list[dict[str, str]]) -> None:
    # From docs/ML_PERSONALIZED_WATCH_SYSTEM_PLAN.md section 2/3: 100,470
    # total measurements minus 18,550 BASELINE_LEARNING = 81,920.
    assert len(feature_rows) == 81920
