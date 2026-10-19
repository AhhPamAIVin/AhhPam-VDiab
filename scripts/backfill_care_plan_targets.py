"""Backfill PatientProfile and CarePlanTarget for patients already imported by
migrate_nhanes_demo_data.py.

That script deliberately drops demographics, comorbidity flags, and the
doctor-approved glucose thresholds when it migrates the synthetic dataset
(patients/models.py's PatientProfile and CarePlanTarget did not exist yet).
Without this backfill, no patient in the live database can be evaluated by
health/threshold_service.py at all — it abstains (threshold_status=
"UNAVAILABLE") for every reading, because there is nothing to read a doctor-
approved danger_low/danger_high from. See
docs/ML_PERSONALIZED_WATCH_SYSTEM_PLAN.md section 6.

Same conventions as migrate_nhanes_demo_data.py: deterministic ids via
`uuid.uuid5(NAMESPACE_URL, "nhanes:<kind>:<source id>")` so re-running this is
a no-op, not a duplicate-inserting one. Only touches patients that
migrate_nhanes_demo_data.py already created (looked up by that same
deterministic patient id) — never creates new User rows.

Usage:

    python -m scripts.backfill_care_plan_targets [--dry-run]

Requires migrate_nhanes_demo_data.py to have already run (patients + doctor
assignments must exist).
"""

from __future__ import annotations

import argparse
import csv
import logging
import uuid
from datetime import datetime
from pathlib import Path

from sqlmodel import Session, select

import codebase.backend.appointments.models as _appointments_models  # noqa: F401  (registers Appointment — ClinicalNote.appointment_id FKs into it, same as migrate_nhanes_demo_data.py)
from codebase.backend.auth.models import User, UserRole
from codebase.backend.db import create_db_and_tables, engine
from codebase.backend.health.models import GlucoseRecord  # noqa: F401  (registers glucose_records into metadata)
from codebase.backend.medications.models import Medication  # noqa: F401  (registers medications into metadata)
from codebase.backend.patients.models import (  # noqa: F401
    CarePlanTarget,
    ClinicalNote,
    PatientAssignment,
    PatientProfile,
    RiskGroup,
)

logger = logging.getLogger("scripts.backfill_care_plan_targets")

REPO_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = REPO_ROOT / "data" / "knowledge_structured"
DEPLOY_DIR = REPO_ROOT / "data" / "NHANES" / "deployment_dataset"

MONITORING_PROFILES_CSV = KNOWLEDGE_DIR / "patient_cgm_monitoring_profiles.csv"
PATIENT_PROFILES_CSV = DEPLOY_DIR / "patient_profiles.csv"
BASELINE_CLINICAL_CSV = DEPLOY_DIR / "patient_baseline_clinical.csv"


def _det_id(*parts: str) -> uuid.UUID:
    # Must match migrate_nhanes_demo_data.py's own formula exactly — this is
    # how a source patient_id resolves to the same User.id that script created.
    return uuid.uuid5(uuid.NAMESPACE_URL, ":".join(("nhanes", *parts)))


def _load_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _truthy(v: str | None) -> bool | None:
    if v is None or v == "":
        return None
    return v.strip().lower() == "true"


def _existing_ids(session: Session, model_cls: type, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not ids:
        return set()
    return set(session.exec(select(model_cls.id).where(model_cls.id.in_(ids))).all())


def _write_batch(session: Session, model_cls: type, rows: list[dict], *, dry_run: bool) -> tuple[int, int]:
    if not rows:
        return 0, 0
    existing = _existing_ids(session, model_cls, [r["id"] for r in rows])
    new_rows = [r for r in rows if r["id"] not in existing]
    if dry_run:
        return len(rows), len(new_rows)
    session.add_all(model_cls(**r) for r in new_rows)
    session.commit()
    return len(rows), len(new_rows)


def _build_patient_profile_rows(profile_rows: list[dict[str, str]], known_patient_ids: set[uuid.UUID]) -> list[dict]:
    rows = []
    for row in profile_rows:
        p_uuid = _det_id("patient", row["patient_id"])
        if p_uuid not in known_patient_ids:
            continue
        rows.append({
            "id": _det_id("patient_profile", row["patient_id"]),
            "patient_id": p_uuid,
            "age_years": float(row["age_years"]) if row.get("age_years") else None,
            "sex": row.get("sex") or None,
            "bmi": float(row["bmi"]) if row.get("bmi") else None,
            "caregiver_required_flag": _truthy(row.get("caregiver_required_flag")),
        })
    return rows


def _apply_comorbidity_flags(rows: list[dict], baseline_rows: list[dict[str, str]]) -> None:
    """Mutates `rows` (from _build_patient_profile_rows) in place — separate
    CSV, joined by source patient_id, not worth a second write pass."""
    baseline_by_patient = {r["patient_id"]: r for r in baseline_rows}
    by_patient_id = {_det_id("patient_profile", pid): pid for pid in baseline_by_patient}
    for row in rows:
        source_pid = by_patient_id.get(row["id"])
        baseline = baseline_by_patient.get(source_pid) if source_pid else None
        if baseline is None:
            continue
        row["congestive_heart_failure_flag"] = _truthy(baseline.get("congestive_heart_failure_flag"))
        row["coronary_heart_disease_flag"] = _truthy(baseline.get("coronary_heart_disease_flag"))
        row["stroke_flag"] = _truthy(baseline.get("stroke_flag"))
        row["family_history_diabetes_flag"] = _truthy(baseline.get("family_history_diabetes_flag"))


def _build_care_plan_target_rows(
    monitoring_rows: list[dict[str, str]],
    known_patient_ids: set[uuid.UUID],
    doctor_by_patient: dict[uuid.UUID, uuid.UUID],
    admin_id: uuid.UUID | None,
) -> list[dict]:
    rows = []
    for row in monitoring_rows:
        p_uuid = _det_id("patient", row["patient_id"])
        if p_uuid not in known_patient_ids:
            continue
        approver = doctor_by_patient.get(p_uuid, admin_id)
        if approver is None:
            continue
        rows.append({
            "id": _det_id("care_plan_target", row["patient_id"]),
            "patient_id": p_uuid,
            "approved_by_doctor_id": approver,
            "risk_group": RiskGroup(row["risk_group"]),
            "target_low_mg_dl": float(row["normal_low_mg_dl"]),
            "target_high_mg_dl": float(row["normal_high_mg_dl"]),
            "danger_low_mg_dl": float(row["danger_low_mg_dl"]),
            "danger_high_mg_dl": float(row["danger_high_mg_dl"]),
            "version": 1,
            "effective_from": datetime(2026, 1, 1),
            "change_reason": "Initial synthetic clinician-approved threshold import",
        })
    return rows


def run_backfill(*, dry_run: bool = False) -> dict[str, tuple[int, int]]:
    create_db_and_tables()
    summary: dict[str, tuple[int, int]] = {}

    with Session(engine) as session:
        known_patient_ids = set(session.exec(select(User.id).where(User.role == UserRole.PATIENT)).all())
        if not known_patient_ids:
            raise RuntimeError(
                "No patient users found — run `python -m scripts.migrate_nhanes_demo_data` first."
            )
        doctor_by_patient = dict(
            session.exec(select(PatientAssignment.patient_id, PatientAssignment.doctor_id)).all()
        )
        admin = session.exec(select(User).where(User.role == UserRole.ADMIN)).first()

        profile_rows = _build_patient_profile_rows(_load_csv_rows(PATIENT_PROFILES_CSV), known_patient_ids)
        _apply_comorbidity_flags(profile_rows, _load_csv_rows(BASELINE_CLINICAL_CSV))
        summary["patient_profiles"] = _write_batch(session, PatientProfile, profile_rows, dry_run=dry_run)

        target_rows = _build_care_plan_target_rows(
            _load_csv_rows(MONITORING_PROFILES_CSV), known_patient_ids, doctor_by_patient,
            admin.id if admin else None,
        )
        summary["care_plan_targets"] = _write_batch(session, CarePlanTarget, target_rows, dry_run=dry_run)

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    summary = run_backfill(dry_run=args.dry_run)
    for table, (prepared, changed) in summary.items():
        verb = "would insert" if args.dry_run else "inserted"
        logger.info("%s: %d prepared, %d %s", table, prepared, changed, verb)


if __name__ == "__main__":
    main()
