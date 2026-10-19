#!/usr/bin/env python3
"""Build auditable, structured reference data from the Markdown knowledge base.

This pipeline never assigns clinical facts, treatment, or diagnoses to patients.
Patient-facing recommendation rules are exported disabled and require clinical
approval before an application may execute them.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


PIPELINE_VERSION = "knowledge_base_structuring_v1.0"
RULESET_VERSION = "diabetes_reference_rules_v1.0-draft"

DRUG_CLASSES = {
    "acarbose": ("A10BF01", "ALPHA_GLUCOSIDASE_INHIBITOR"),
    "dapagliflozin": ("A10BK01", "SGLT2_INHIBITOR"),
    "empagliflozin": ("A10BK03", "SGLT2_INHIBITOR"),
    "gliclazide": ("", "SULFONYLUREA"),
    "glimepiride": ("A10BB12", "SULFONYLUREA"),
    "insulin": ("A10A", "INSULIN"),
    "liraglutide": ("", "GLP1_RECEPTOR_AGONIST"),
    "metformin": ("A10BA02", "BIGUANIDE"),
    "pioglitazone": ("", "THIAZOLIDINEDIONE"),
    "repaglinide": ("A10BX02", "MEGLITINIDE"),
    "semaglutide": ("A10BJ06", "GLP1_RECEPTOR_AGONIST"),
    "sitagliptin": ("A10BH01", "DPP4_INHIBITOR"),
    "vildagliptin": ("", "DPP4_INHIBITOR"),
}

DOCUMENT_TYPE_RULES = [
    ("duoc-thu", "DRUG_MONOGRAPH"),
    ("toan-van", "CLINICAL_GUIDELINE_FULLTEXT"),
    ("iwgdf", "CLINICAL_GUIDELINE_FULLTEXT"),
    ("dinh-duong", "PATIENT_EDUCATION_NUTRITION"),
    ("che-do-an", "PATIENT_EDUCATION_NUTRITION"),
    ("van-dong", "PATIENT_EDUCATION_ACTIVITY"),
    ("luyen-tap", "PATIENT_EDUCATION_ACTIVITY"),
    ("hoat-dong", "PATIENT_EDUCATION_ACTIVITY"),
    ("ban-chan", "PATIENT_EDUCATION_FOOT_CARE"),
]

EDUCATION_TOPICS = [
    ("EDU-NUTRITION", "NUTRITION", "Dinh dưỡng và chia bữa", ["dinh-duong", "che-do-an"]),
    ("EDU-ACTIVITY", "PHYSICAL_ACTIVITY", "Vận động an toàn", ["van-dong", "luyen-tap", "hoat-dong"]),
    ("EDU-FOOT", "FOOT_CARE", "Chăm sóc và phòng loét bàn chân", ["ban-chan"]),
    ("EDU-EYE", "EYE_SCREENING", "Khám mắt và võng mạc", ["kham-mat"]),
    ("EDU-SICK-DAY", "SICK_DAY", "Quản lý ngày bị bệnh", ["sick-day", "ngay-bi-benh"]),
    ("EDU-MENTAL", "MENTAL_HEALTH", "Stress, lo âu và trầm cảm", ["tam-ly", "tram-cam"]),
    ("EDU-HYPER", "HYPERGLYCEMIA", "Nhận biết tăng đường huyết nguy hiểm", ["tang-duong-huyet"]),
    ("EDU-MED-SAFETY", "MEDICATION_SAFETY", "Thuốc và thay đổi glucose", ["thuoc-khac", "duoc-thu"]),
    ("EDU-GI", "GLYCEMIC_INDEX", "Chỉ số đường huyết của thực phẩm", ["chi-so-duong-huyet", "chi-so-gi"]),
]

TARGET_TEMPLATES = [
    ("TARGET-BYT-ADULT-A1C", "BYT_ADULT_NONPREGNANT", "HBA1C", "<", "7", "%", "Bảng 4; cá thể hóa", "byt-2020-qd-5481-huong-dan-dai-thao-duong-tip-2-toan-van.md", "lines 809-830"),
    ("TARGET-BYT-ADULT-FASTING", "BYT_ADULT_NONPREGNANT", "FASTING_GLUCOSE", "BETWEEN", "80|130", "mg/dL", "Đói hoặc trước ăn", "byt-2020-qd-5481-huong-dan-dai-thao-duong-tip-2-toan-van.md", "lines 809-830"),
    ("TARGET-BYT-ADULT-POSTMEAL", "BYT_ADULT_NONPREGNANT", "POSTMEAL_GLUCOSE_1_2H", "<", "180", "mg/dL", "Sau bắt đầu ăn 1-2 giờ", "byt-2020-qd-5481-huong-dan-dai-thao-duong-tip-2-toan-van.md", "lines 809-830"),
    ("TARGET-ADA26-OA-HEALTHY-A1C", "OLDER_ADULT_HEALTHY", "HBA1C", "<", "7.0|7.5", "%", "Khoảng mục tiêu tùy cá thể", "ada-2026-muc-tieu-duong-huyet-nguoi-cao-tuoi.md", "Khuyến nghị 13.7a"),
    ("TARGET-ADA26-OA-HEALTHY-TIR", "OLDER_ADULT_HEALTHY", "TIR_70_180", ">=", "70", "%", "CGM target", "ada-2026-muc-tieu-duong-huyet-nguoi-cao-tuoi.md", "Khuyến nghị 13.7a"),
    ("TARGET-ADA26-OA-COMPLEX-A1C", "OLDER_ADULT_COMPLEX_INTERMEDIATE", "HBA1C", "<", "8.0", "%", "Cần cá thể hóa", "ada-2026-muc-tieu-duong-huyet-nguoi-cao-tuoi.md", "Khuyến nghị 13.7b"),
    ("TARGET-ADA26-OA-COMPLEX-TIR", "OLDER_ADULT_COMPLEX_INTERMEDIATE", "TIR_70_180", ">=", "50", "%", "CGM target", "ada-2026-muc-tieu-duong-huyet-nguoi-cao-tuoi.md", "Khuyến nghị 13.7b"),
    ("TARGET-ADA26-OA-POOR-A1C", "OLDER_ADULT_VERY_COMPLEX_POOR", "HBA1C", "NO_FIXED_TARGET", "", "%", "Tránh phụ thuộc A1C; ưu tiên tránh triệu chứng", "ada-2026-muc-tieu-duong-huyet-nguoi-cao-tuoi.md", "Khuyến nghị 13.7c"),
]

SCREENING_PROTOCOLS = [
    ("SCREEN-COMPREHENSIVE", "COMPREHENSIVE_DIABETES_REVIEW", "AT_BASELINE_AND_PERIODIC", "Đánh giá toàn diện lần đầu và định kỳ", "byt-2020-qd-5481-huong-dan-dai-thao-duong-tip-2-toan-van.md", "lines 620-711"),
    ("SCREEN-KIDNEY", "KIDNEY_UACR_EGFR", "AT_LEAST_ANNUALLY", "UACR và eGFR; theo dõi sát hơn khi bất thường", "byt-2020-qd-5481-huong-dan-dai-thao-duong-tip-2-toan-van.md", "lines 2044-2071"),
    ("SCREEN-EYE", "DIABETIC_RETINOPATHY", "PER_GUIDELINE_AND_EYE_SPECIALIST", "Tầm soát võng mạc và theo dõi theo kết quả", "byt-2020-qd-5481-huong-dan-dai-thao-duong-tip-2-toan-van.md", "lines 2019-2033"),
    ("SCREEN-NEURO", "PERIPHERAL_NEUROPATHY", "AT_DIABETES_CARE_ENTRY_AND_PERIODIC", "Đánh giá bệnh thần kinh ngoại biên", "byt-2020-qd-5481-huong-dan-dai-thao-duong-tip-2-toan-van.md", "lines 2104-2120"),
    ("SCREEN-FOOT", "COMPREHENSIVE_FOOT_EXAM", "RISK_STRATIFIED", "Khám bàn chân và phân tầng nguy cơ", "byt-2023-qd-1530-loet-ban-chan-dai-thao-duong-toan-van.md", "sections 3-4"),
    ("SCREEN-MENTAL", "DEPRESSION_ANXIETY_SOCIAL_SUPPORT", "PERIODIC", "Tầm soát trầm cảm, lo âu, tài chính và hỗ trợ xã hội", "byt-2020-qd-5481-huong-dan-dai-thao-duong-tip-2-toan-van.md", "lines 620-655"),
]

RECOMMENDATION_RULES = [
    ("RULE-KIDNEY-DUE", "KIDNEY_SCREENING_REVIEW", "TYPE_2", "missing_or_outdated kidney assessment", "SCREEN-KIDNEY"),
    ("RULE-EYE-DUE", "EYE_SCREENING_REVIEW", "TYPE_2", "missing_or_outdated eye assessment", "SCREEN-EYE"),
    ("RULE-FOOT-DUE", "FOOT_SCREENING_REVIEW", "TYPE_2", "missing_or_outdated foot assessment", "SCREEN-FOOT"),
    ("RULE-MENTAL-DUE", "MENTAL_HEALTH_SCREENING_REVIEW", "TYPE_2", "missing_or_outdated mental health screening", "SCREEN-MENTAL"),
    ("RULE-TARGET-REVIEW", "CLINICAL_TARGET_REVIEW", "PREDIABETES|TYPE_2", "no clinician-approved active target", "TARGET-BYT-ADULT-A1C"),
    ("RULE-SICK-DAY-EDU", "SICK_DAY_EDUCATION_REVIEW", "TYPE_2", "education not documented", "EDU-SICK-DAY"),
]

# Numeric alert thresholds extracted from the local knowledge base. These are
# application/demo rules only. Production activation requires clinical review
# and may be overridden by an approved patient-specific care plan.
ALERT_THRESHOLD_RULES = [
    {
        "alert_rule_id": "ALERT-GLU-HYPO-L2",
        "rule_code": "GLUCOSE_HYPOGLYCEMIA_LEVEL_2",
        "population_code": "PREDIABETES|TYPE_2",
        "context_code": "ANY_MEASUREMENT",
        "metric_code": "GLUCOSE_MG_DL",
        "operator": "<",
        "threshold_low": "54",
        "threshold_high": "",
        "lower_inclusive": "False",
        "upper_inclusive": "False",
        "severity": "CRITICAL",
        "consecutive_readings": "1",
        "window_minutes": "0",
        "cooldown_minutes": "15",
        "alert_title": "Ha duong huyet muc 2",
        "message_template": "Glucose duoi 54 mg/dL. Can xu tri ha duong huyet khan cap theo ke hoach da duoc phe duyet.",
        "action_code": "URGENT_HYPOGLYCEMIA_RESPONSE",
        "source_filename": "byt-2020-qd-5481-huong-dan-dai-thao-duong-tip-2-toan-van.md",
        "source_locator": "lines 1704-1705",
        "evidence_summary": "BYT level 2 hypoglycemia: glucose below 54 mg/dL.",
    },
    {
        "alert_rule_id": "ALERT-GLU-HYPO-L1",
        "rule_code": "GLUCOSE_HYPOGLYCEMIA_LEVEL_1",
        "population_code": "PREDIABETES|TYPE_2",
        "context_code": "ANY_MEASUREMENT",
        "metric_code": "GLUCOSE_MG_DL",
        "operator": "BETWEEN",
        "threshold_low": "54",
        "threshold_high": "70",
        "lower_inclusive": "True",
        "upper_inclusive": "False",
        "severity": "HIGH",
        "consecutive_readings": "1",
        "window_minutes": "0",
        "cooldown_minutes": "15",
        "alert_title": "Ha duong huyet muc 1",
        "message_template": "Glucose tu 54 den duoi 70 mg/dL. Can xu tri ha duong huyet va do lai theo ke hoach da duoc phe duyet.",
        "action_code": "HYPOGLYCEMIA_RESPONSE_AND_RECHECK",
        "source_filename": "byt-2020-qd-5481-huong-dan-dai-thao-duong-tip-2-toan-van.md",
        "source_locator": "lines 1704-1705",
        "evidence_summary": "BYT level 1 hypoglycemia: glucose below 70 and at least 54 mg/dL.",
    },
    {
        "alert_rule_id": "ALERT-GLU-FASTING-LOW",
        "rule_code": "FASTING_GLUCOSE_BELOW_GENERAL_TARGET",
        "population_code": "TYPE_2_ADULT_NONPREGNANT",
        "context_code": "FASTING_OR_PREMEAL",
        "metric_code": "GLUCOSE_MG_DL",
        "operator": "<",
        "threshold_low": "80",
        "threshold_high": "",
        "lower_inclusive": "False",
        "upper_inclusive": "False",
        "severity": "MEDIUM",
        "consecutive_readings": "1",
        "window_minutes": "0",
        "cooldown_minutes": "120",
        "alert_title": "Duoi muc tieu duong huyet doi chung",
        "message_template": "Glucose doi/truoc an duoi muc tieu chung 80-130 mg/dL; uu tien rule ha duong huyet neu duoi 70 mg/dL.",
        "action_code": "REVIEW_GENERAL_TARGET",
        "source_filename": "byt-2020-qd-5481-huong-dan-dai-thao-duong-tip-2-toan-van.md",
        "source_locator": "lines 809-830",
        "evidence_summary": "BYT general fasting/premeal target is 80-130 mg/dL and must be individualized.",
    },
    {
        "alert_rule_id": "ALERT-GLU-FASTING-HIGH",
        "rule_code": "FASTING_GLUCOSE_ABOVE_GENERAL_TARGET",
        "population_code": "TYPE_2_ADULT_NONPREGNANT",
        "context_code": "FASTING_OR_PREMEAL",
        "metric_code": "GLUCOSE_MG_DL",
        "operator": ">",
        "threshold_low": "130",
        "threshold_high": "",
        "lower_inclusive": "False",
        "upper_inclusive": "False",
        "severity": "MEDIUM",
        "consecutive_readings": "2",
        "window_minutes": "1440",
        "cooldown_minutes": "720",
        "alert_title": "Tren muc tieu duong huyet doi chung",
        "message_template": "Nhieu lan glucose doi/truoc an tren muc tieu chung 130 mg/dL. Can xem lai ke hoach voi nhan vien y te.",
        "action_code": "REVIEW_GENERAL_TARGET",
        "source_filename": "byt-2020-qd-5481-huong-dan-dai-thao-duong-tip-2-toan-van.md",
        "source_locator": "lines 809-830",
        "evidence_summary": "BYT general fasting/premeal target is 80-130 mg/dL and must be individualized.",
    },
    {
        "alert_rule_id": "ALERT-GLU-POSTMEAL-HIGH",
        "rule_code": "POSTMEAL_GLUCOSE_ABOVE_GENERAL_TARGET",
        "population_code": "TYPE_2_ADULT_NONPREGNANT",
        "context_code": "POSTMEAL_1_TO_2_HOURS",
        "metric_code": "GLUCOSE_MG_DL",
        "operator": ">=",
        "threshold_low": "180",
        "threshold_high": "",
        "lower_inclusive": "True",
        "upper_inclusive": "False",
        "severity": "MEDIUM",
        "consecutive_readings": "2",
        "window_minutes": "4320",
        "cooldown_minutes": "720",
        "alert_title": "Tren muc tieu glucose sau an chung",
        "message_template": "Nhieu lan glucose 1-2 gio sau an tu 180 mg/dL tro len. Can xem lai bua an va ke hoach voi nhan vien y te.",
        "action_code": "REVIEW_POSTMEAL_PATTERN",
        "source_filename": "byt-2020-qd-5481-huong-dan-dai-thao-duong-tip-2-toan-van.md",
        "source_locator": "lines 809-830",
        "evidence_summary": "BYT general 1-2 hour postmeal target is below 180 mg/dL.",
    },
    {
        "alert_rule_id": "ALERT-GLU-HYPER-DANGER",
        "rule_code": "DANGEROUS_HYPERGLYCEMIA",
        "population_code": "PREDIABETES|TYPE_2",
        "context_code": "ANY_MEASUREMENT",
        "metric_code": "GLUCOSE_MG_DL",
        "operator": ">=",
        "threshold_low": "250",
        "threshold_high": "",
        "lower_inclusive": "True",
        "upper_inclusive": "False",
        "severity": "HIGH",
        "consecutive_readings": "1",
        "window_minutes": "0",
        "cooldown_minutes": "120",
        "alert_title": "Tang duong huyet nguy hiem",
        "message_template": "Glucose tu 250 mg/dL tro len. Can kiem tra trieu chung dau do/ceton neu co va lam theo ke hoach cham soc.",
        "action_code": "CHECK_RED_FLAGS_KETONES_AND_RECHECK",
        "source_filename": "vade-cap-cuu-tang-duong-huyet-nhan-biet-xu-tri.md",
        "source_locator": "lines 10-18, 46-62, 80-85",
        "evidence_summary": "VADE threshold around 14 mmol/L or 250 mg/dL is cross-checked against BYT 5481.",
    },
    {
        "alert_rule_id": "ALERT-GLU-HYPER-PERSISTENT",
        "rule_code": "DANGEROUS_HYPERGLYCEMIA_NOT_DECREASING",
        "population_code": "PREDIABETES|TYPE_2",
        "context_code": "ANY_MEASUREMENT",
        "metric_code": "GLUCOSE_MG_DL",
        "operator": "PERSISTENT_AT_OR_ABOVE",
        "threshold_low": "250",
        "threshold_high": "",
        "lower_inclusive": "True",
        "upper_inclusive": "False",
        "severity": "CRITICAL",
        "consecutive_readings": "2",
        "window_minutes": "120",
        "cooldown_minutes": "120",
        "alert_title": "Tang duong huyet nguy hiem khong giam",
        "message_template": "Glucose van o muc nguy hiem sau 2 gio hoac khong giam. Can lien he co so y te theo ke hoach da duoc phe duyet.",
        "action_code": "URGENT_CLINICAL_CONTACT",
        "source_filename": "vade-cap-cuu-tang-duong-huyet-nhan-biet-xu-tri.md",
        "source_locator": "lines 46-62",
        "evidence_summary": "VADE advises urgent contact if high glucose does not decrease after two hours or red flags occur.",
    },
    {
        "alert_rule_id": "ALERT-GLU-HYPER-EXTREME",
        "rule_code": "EXTREME_HYPERGLYCEMIA_HHS_THRESHOLD",
        "population_code": "PREDIABETES|TYPE_2",
        "context_code": "ANY_MEASUREMENT",
        "metric_code": "GLUCOSE_MG_DL",
        "operator": ">=",
        "threshold_low": "600",
        "threshold_high": "",
        "lower_inclusive": "True",
        "upper_inclusive": "False",
        "severity": "CRITICAL",
        "consecutive_readings": "1",
        "window_minutes": "0",
        "cooldown_minutes": "60",
        "alert_title": "Tang duong huyet rat nang",
        "message_template": "Glucose tu 600 mg/dL tro len nam trong nguong lien quan tang ap luc tham thau. Can danh gia cap cuu.",
        "action_code": "EMERGENCY_EVALUATION",
        "source_filename": "byt-2020-qd-5481-huong-dan-dai-thao-duong-tip-2-toan-van.md",
        "source_locator": "lines 1830-1835",
        "evidence_summary": "BYT table lists glucose above 33.3 mmol/L (about 600 mg/dL) for hyperosmolar state.",
    },
    {
        "alert_rule_id": "ALERT-EXERCISE-GLU-LOW",
        "rule_code": "EXERCISE_PRECHECK_GLUCOSE_LOW",
        "population_code": "TYPE_2",
        "context_code": "BEFORE_EXERCISE",
        "metric_code": "GLUCOSE_MG_DL",
        "operator": "<",
        "threshold_low": "99",
        "threshold_high": "",
        "lower_inclusive": "False",
        "upper_inclusive": "False",
        "severity": "HIGH",
        "consecutive_readings": "1",
        "window_minutes": "0",
        "cooldown_minutes": "60",
        "alert_title": "Khong bat dau van dong khi glucose thap",
        "message_template": "Glucose truoc tap duoi 5.5 mmol/L (xap xi 99 mg/dL). Tam hoan van dong va lam theo huong dan an toan da duoc phe duyet.",
        "action_code": "PAUSE_EXERCISE_AND_REVIEW",
        "source_filename": "byt-2020-qd-5481-huong-dan-dai-thao-duong-tip-2-toan-van.md",
        "source_locator": "lines 1151-1177",
        "evidence_summary": "BYT advises no exercise when glucose is below 5.5 mmol/L.",
    },
    {
        "alert_rule_id": "ALERT-EXERCISE-GLU-HIGH",
        "rule_code": "EXERCISE_PRECHECK_GLUCOSE_HIGH",
        "population_code": "TYPE_2",
        "context_code": "BEFORE_EXERCISE",
        "metric_code": "GLUCOSE_MG_DL",
        "operator": ">",
        "threshold_low": "252",
        "threshold_high": "",
        "lower_inclusive": "False",
        "upper_inclusive": "False",
        "severity": "HIGH",
        "consecutive_readings": "1",
        "window_minutes": "0",
        "cooldown_minutes": "60",
        "alert_title": "Khong bat dau van dong khi glucose cao",
        "message_template": "Glucose truoc tap tren 14.0 mmol/L (xap xi 252 mg/dL). Tam hoan van dong va danh gia an toan.",
        "action_code": "PAUSE_EXERCISE_AND_REVIEW",
        "source_filename": "byt-2020-qd-5481-huong-dan-dai-thao-duong-tip-2-toan-van.md",
        "source_locator": "lines 1151-1177",
        "evidence_summary": "BYT advises no exercise when glucose is above 14.0 mmol/L.",
    },
]


def slug_id(prefix: str, value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12].upper()
    return f"{prefix}-{digest}"


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def source_metadata(lines: list[str]) -> tuple[str, str]:
    source_url = ""
    crawl_date = ""
    for line in lines[:12]:
        if line.startswith("> Nguồn:"):
            source_url = line.split(":", 1)[1].strip().split()[0]
        if "Crawl ngày:" in line or "Trích xuất ngày:" in line:
            match = re.search(r"(20\d{2}-\d{2}-\d{2})", line)
            if match:
                crawl_date = match.group(1)
    return source_url, crawl_date


def document_type(filename: str) -> str:
    lowered = filename.lower()
    for token, doc_type in DOCUMENT_TYPE_RULES:
        if token in lowered:
            return doc_type
    return "PATIENT_EDUCATION_GENERAL"


def split_sections(lines: list[str]) -> list[tuple[str, int, int, str]]:
    starts = [i for i, line in enumerate(lines) if re.match(r"^#{1,4}\s+", line)]
    if not starts:
        return [("FULL_DOCUMENT", 1, len(lines), "\n".join(lines).strip())]
    sections = []
    for pos, start in enumerate(starts):
        end = starts[pos + 1] if pos + 1 < len(starts) else len(lines)
        heading = re.sub(r"^#{1,4}\s+", "", lines[start]).strip()
        body = "\n".join(lines[start + 1 : end]).strip()
        sections.append((heading, start + 1, end, body))
    return sections


def chunk_text(text: str, max_chars: int = 1800) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return [""]
    chunks, current = [], ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            pieces = [paragraph[i : i + max_chars] for i in range(0, len(paragraph), max_chars)]
        else:
            pieces = [paragraph]
        for piece in pieces:
            candidate = f"{current}\n\n{piece}".strip()
            if current and len(candidate) > max_chars:
                chunks.append(current)
                current = piece
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def build(knowledge_dir: Path, output_dir: Path) -> dict:
    markdown_files = sorted(knowledge_dir.glob("*.md"))
    if not markdown_files:
        raise FileNotFoundError(f"No Markdown files found in {knowledge_dir}")

    documents, chunks, drug_catalog, drug_sections = [], [], [], []
    filename_to_id = {}
    for path in markdown_files:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        title = next((re.sub(r"^#\s+", "", x).strip() for x in lines if x.startswith("# ")), path.stem)
        source_url, crawl_date = source_metadata(lines)
        doc_id = slug_id("DOC", path.name)
        filename_to_id[path.name] = doc_id
        doc_type = document_type(path.name)
        documents.append({
            "document_id": doc_id,
            "filename": path.name,
            "title": title,
            "document_type": doc_type,
            "source_url": source_url,
            "source_organization": "",
            "published_date": "",
            "retrieved_date": crawl_date,
            "language": "vi",
            "content_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "line_count": len(lines),
            "status": "REFERENCE_ONLY",
            "pipeline_version": PIPELINE_VERSION,
        })
        section_number = 0
        for heading, line_start, line_end, body in split_sections(lines):
            for chunk_number, content in enumerate(chunk_text(body), 1):
                if not content:
                    continue
                section_number += 1
                chunks.append({
                    "chunk_id": slug_id("CHUNK", f"{path.name}|{heading}|{chunk_number}|{content[:80]}"),
                    "document_id": doc_id,
                    "heading": heading,
                    "chunk_sequence": section_number,
                    "source_line_start": line_start,
                    "source_line_end": line_end,
                    "content": content,
                    "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "embedding_status": "NOT_GENERATED",
                    "pipeline_version": PIPELINE_VERSION,
                })

        if path.name.startswith("duoc-thu-qg-2022-"):
            drug_key = path.name.removeprefix("duoc-thu-qg-2022-").removesuffix("-chuyen-luan.md")
            atc, drug_class = DRUG_CLASSES[drug_key]
            drug_id = f"DRUG-{drug_key.upper()}"
            drug_catalog.append({
                "drug_reference_id": drug_id,
                "generic_name": drug_key.upper(),
                "atc_code": atc,
                "drug_class": drug_class,
                "document_id": doc_id,
                "reference_status": "REFERENCE_ONLY_NOT_A_PRESCRIPTION",
                "pipeline_version": PIPELINE_VERSION,
            })
            for heading, line_start, line_end, body in split_sections(lines):
                if not body or heading == title:
                    continue
                drug_sections.append({
                    "drug_section_id": slug_id("DRUGSEC", f"{drug_id}|{heading}"),
                    "drug_reference_id": drug_id,
                    "section_type": heading.upper().replace(" ", "_")[:100],
                    "section_title": heading,
                    "content": body,
                    "source_line_start": line_start,
                    "source_line_end": line_end,
                    "pipeline_version": PIPELINE_VERSION,
                })

    target_rows = []
    for target_id, population, metric, operator, value, unit, note, filename, locator in TARGET_TEMPLATES:
        target_rows.append({
            "target_template_id": target_id,
            "population_code": population,
            "metric_code": metric,
            "operator": operator,
            "target_value": value,
            "unit": unit,
            "clinical_note": note,
            "document_id": filename_to_id[filename],
            "source_locator": locator,
            "requires_individualization": True,
            "approval_status": "REFERENCE_APPROVED_SOURCE_NOT_PATIENT_ORDER",
            "pipeline_version": PIPELINE_VERSION,
        })

    screening_rows = []
    for protocol_id, code, interval, summary, filename, locator in SCREENING_PROTOCOLS:
        screening_rows.append({
            "screening_protocol_id": protocol_id,
            "screening_code": code,
            "recommended_interval": interval,
            "summary": summary,
            "document_id": filename_to_id[filename],
            "source_locator": locator,
            "requires_clinical_judgment": True,
            "pipeline_version": PIPELINE_VERSION,
        })

    education_rows, education_links = [], []
    for topic_id, code, title, tokens in EDUCATION_TOPICS:
        education_rows.append({
            "education_topic_id": topic_id,
            "topic_code": code,
            "title": title,
            "content_status": "REFERENCE_CONTENT",
            "clinical_review_status": "REQUIRES_LOCAL_REVIEW_BEFORE_PATIENT_ASSIGNMENT",
            "pipeline_version": PIPELINE_VERSION,
        })
        for filename, doc_id in filename_to_id.items():
            if any(token in filename.lower() for token in tokens):
                education_links.append({
                    "education_topic_id": topic_id,
                    "document_id": doc_id,
                    "link_type": "SUPPORTING_DOCUMENT",
                    "pipeline_version": PIPELINE_VERSION,
                })

    rule_rows = []
    for rule_id, rule_type, population, condition, reference_id in RECOMMENDATION_RULES:
        rule_rows.append({
            "recommendation_rule_id": rule_id,
            "rule_type": rule_type,
            "population_filter": population,
            "condition_expression": condition,
            "reference_id": reference_id,
            "rule_version": RULESET_VERSION,
            "enabled": False,
            "approval_status": "DRAFT_REQUIRES_CLINICAL_APPROVAL",
            "automated_action_allowed": False,
            "output_scope": "REVIEW_PROMPT_ONLY_NOT_DIAGNOSIS_OR_TREATMENT",
            "pipeline_version": PIPELINE_VERSION,
        })

    alert_rule_rows = []
    for source_rule in ALERT_THRESHOLD_RULES:
        rule = dict(source_rule)
        source_filename = rule.pop("source_filename")
        rule.update({
            "unit": "mg/dL",
            "document_id": filename_to_id[source_filename],
            "evidence_confidence": "HIGH",
            "demo_enabled": True,
            "production_enabled": False,
            "requires_clinical_approval": True,
            "patient_specific_override_allowed": True,
            "automated_medication_change_allowed": False,
            "rule_status": "REFERENCE_DERIVED_DRAFT",
            "rule_version": RULESET_VERSION,
            "pipeline_version": PIPELINE_VERSION,
        })
        alert_rule_rows.append(rule)

    outputs = {
        "knowledge_documents.csv": (documents, list(documents[0])),
        "knowledge_chunks.csv": (chunks, list(chunks[0])),
        "drug_catalog.csv": (drug_catalog, list(drug_catalog[0])),
        "drug_reference_sections.csv": (drug_sections, list(drug_sections[0])),
        "clinical_target_templates.csv": (target_rows, list(target_rows[0])),
        "screening_protocols.csv": (screening_rows, list(screening_rows[0])),
        "education_topics.csv": (education_rows, list(education_rows[0])),
        "education_topic_documents.csv": (education_links, list(education_links[0])),
        "recommendation_rules.csv": (rule_rows, list(rule_rows[0])),
        "alert_threshold_rules.csv": (alert_rule_rows, list(alert_rule_rows[0])),
    }
    for filename, (rows, fields) in outputs.items():
        write_csv(output_dir / filename, fields, rows)

    template_fields = {
        "patient_kidney_assessments_template.csv": ["kidney_assessment_id", "patient_id", "assessed_at", "creatinine_mg_dl", "egfr_ml_min_1_73m2", "uacr_mg_g", "source_type", "clinician_id", "record_status"],
        "patient_eye_exams_template.csv": ["eye_exam_id", "patient_id", "examined_at", "retinopathy_status", "laterality", "exam_method", "clinician_id", "next_review_date", "record_status"],
        "patient_foot_exams_template.csv": ["foot_exam_id", "patient_id", "examined_at", "protective_sensation_status", "pulse_status", "deformity_status", "ulcer_status", "risk_classification", "clinician_id", "record_status"],
        "patient_mental_health_screenings_template.csv": ["screening_id", "patient_id", "screened_at", "instrument_code", "score", "interpretation", "clinician_id", "record_status"],
        "patient_care_plans_template.csv": ["care_plan_id", "patient_id", "created_at", "effective_from", "effective_to", "status", "approved_by_doctor_id", "approval_timestamp", "notes"],
        "patient_clinical_targets_template.csv": ["patient_target_id", "care_plan_id", "patient_id", "metric_code", "operator", "target_value", "unit", "source_template_id", "approved_by_doctor_id", "status"],
        "patient_education_progress_template.csv": ["education_progress_id", "patient_id", "education_topic_id", "assigned_at", "assigned_by", "status", "completed_at", "acknowledged_at"],
    }
    for filename, fields in template_fields.items():
        write_csv(output_dir / "templates" / filename, fields, [])

    file_manifest = {}
    for path in sorted(output_dir.rglob("*.csv")):
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            count = sum(1 for _ in csv.reader(handle)) - 1
        file_manifest[str(path.relative_to(output_dir)).replace("\\", "/")] = {
            "rows": max(count, 0),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }
    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": PIPELINE_VERSION,
        "ruleset_version": RULESET_VERSION,
        "source_markdown_files": len(markdown_files),
        "source_image_files": len(list((knowledge_dir / "images").glob("*"))),
        "document_type_counts": dict(Counter(row["document_type"] for row in documents)),
        "safety_policy": {
            "patient_facts_inferred_from_knowledge_base": False,
            "recommendation_rules_enabled": False,
            "clinical_approval_required": True,
            "automated_treatment_allowed": False,
        },
        "files": file_manifest,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--knowledge-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest = build(args.knowledge_dir, args.output_dir)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
