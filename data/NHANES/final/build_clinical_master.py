from __future__ import annotations

"""Rebuild the NHANES clinical master from raw XPT files.

The established merge logic remains in merge.ipynb for auditability. This runner
executes only the canonical pipeline cells in a fixed order, enriches the result
with NHANES survey-design variables, and writes the all-ages master consumed by
the cleaning pipeline.
"""

import json
import re
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "merge.ipynb"
OUTPUT = ROOT / "processed" / "nhanes_clinical_master_all_ages.csv"


def find_cell(cells: list[dict], required_fragments: list[str]) -> str:
    matches = []
    for cell in cells:
        if cell.get("cell_type") != "code":
            continue
        source = "".join(cell.get("source", []))
        if all(fragment in source for fragment in required_fragments):
            matches.append(source)
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one canonical notebook cell for {required_fragments}, "
            f"found {len(matches)}"
        )
    return matches[0]


def add_survey_design(master: pd.DataFrame) -> pd.DataFrame:
    demo = pd.read_sas(ROOT / "P_DEMO.xpt", format="xport", encoding=None)
    design = demo[
        ["SEQN", "WTINTPRP", "WTMECPRP", "SDMVPSU", "SDMVSTRA"]
    ].rename(
        columns={
            "SEQN": "patient_id",
            "WTINTPRP": "interview_weight",
            "WTMECPRP": "mec_exam_weight",
            "SDMVPSU": "survey_psu",
            "SDMVSTRA": "survey_stratum",
        }
    )
    fasting = pd.read_sas(ROOT / "P_GLU.xpt", format="xport", encoding=None)[
        ["SEQN", "WTSAFPRP"]
    ].rename(
        columns={"SEQN": "patient_id", "WTSAFPRP": "fasting_subsample_weight"}
    )
    design["patient_id"] = design["patient_id"].astype("Int64")
    fasting["patient_id"] = fasting["patient_id"].astype("Int64")
    result = master.merge(design, on="patient_id", how="left", validate="one_to_one")
    result = result.merge(fasting, on="patient_id", how="left", validate="one_to_one")
    return result


def main() -> None:
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    cells = notebook["cells"]
    ordered_sources = [
        find_cell(cells, ["EXPECTED_FILES =", "COLUMN_MAPS", "clinical_path"]),
        find_cell(cells, ["clinical_all_ages = clinical.copy()", "age_group"]),
        find_cell(cells, ["caregiver_mode", "Primary caregiver"]),
        find_cell(cells, ["smoking_applicable", "adult_questionnaire_applicable"]),
    ]

    namespace: dict[str, object] = {"__name__": "nhanes_master_pipeline"}
    for source in ordered_sources:
        source = re.sub(
            r'DATA_DIR\s*=\s*Path\(r?"[^"]+"\)',
            lambda _: f'DATA_DIR = Path(r"{ROOT}")',
            source,
        )
        exec(compile(source, str(NOTEBOOK), "exec"), namespace)

    master = namespace.get("clinical_all_ages")
    if not isinstance(master, pd.DataFrame):
        raise RuntimeError("Canonical notebook pipeline did not produce clinical_all_ages")
    master = add_survey_design(master)
    if len(master) != master["patient_id"].nunique():
        raise ValueError("Master patient_id must be unique")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    master.to_csv(OUTPUT, index=False, encoding="utf-8-sig")
    print(f"Saved {OUTPUT} with {len(master)} rows and {len(master.columns)} columns")


if __name__ == "__main__":
    main()
