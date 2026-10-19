"""Unit tests for codebase/models/rag/chunking.py.

Pure string-in/dict-out logic, no embeddings API and no chromadb — these
tests exercise Track A (##-boundary) and Track B (page-marker / pseudo-
header / table-atomicity) chunking directly, including against real excerpts
of the corpus per docs/AI_Summary_Respond_System_Design.md §5.2, and the
sources.csv skip/build logic per §5.1.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

import pytest

from codebase.models.rag import chunking as ck

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "knowledge_base" / "raw"
SOURCES_CSV = REPO_ROOT / "data" / "knowledge_base" / "sources.csv"


# --- track assignment ---------------------------------------------------------


def test_track_mapping_covers_every_real_corpus_file():
    """Regression guard for the design doc's own file-count claim: direct
    inspection (os.listdir, not eyeballing `ls` output) shows 45 files on
    disk, not the 44 the design doc's prose states. This asserts the
    explicit TRACK_A_FILES/TRACK_B_FILES mapping in chunking.py stays in
    sync with whatever is actually in raw/ — if a file is added or removed
    this test fails loudly instead of chunk_tier2_file silently mis-skipping it."""
    on_disk = {f for f in os.listdir(RAW_DIR) if f.endswith(".md")}
    mapped = ck.TRACK_A_FILES | ck.TRACK_B_FILES
    assert on_disk == mapped


def test_classify_file_unknown_raises():
    with pytest.raises(ValueError, match="not in TRACK_A_FILES"):
        ck.classify_file("some-new-file-nobody-added-yet.md")


def test_classify_file_known():
    assert ck.classify_file("byt-5481-qd-byt-full-text.md") == "B"
    assert ck.classify_file("vade-chi-so-gi.md") == "A"


# --- Track A: real short file (vade-chi-so-gi.md) --------------------------


@pytest.fixture
def vade_chi_so_gi_content() -> str:
    return (RAW_DIR / "vade-chi-so-gi.md").read_text(encoding="utf-8")


def test_track_a_splits_long_preamble_into_windows(vade_chi_so_gi_content):
    """This file has a single H1 and no ## until a "Ghi chú đối chiếu"
    section near the end, so everything under the H1 is one ~4,450-token
    section (measured directly) that must fall back to windowed chunking
    per §5.2's "~800-token window with ~100-token overlap"."""
    chunks = ck.chunk_track_a("vade-chi-so-gi.md", vade_chi_so_gi_content)
    assert len(chunks) > 1
    for c in chunks[:-1]:  # last section (Ghi chú) is short, not windowed
        assert ck.estimate_tokens(c["text"]) <= ck.TRACK_A_MAX_TOKENS + 50  # small slack for atomic paragraphs


def test_track_a_char_offsets_exactly_match_source(vade_chi_so_gi_content):
    chunks = ck.chunk_track_a("vade-chi-so-gi.md", vade_chi_so_gi_content)
    for c in chunks:
        assert vade_chi_so_gi_content[c["char_start"] : c["char_end"]] == c["text"]


def test_track_a_last_section_gets_its_own_heading(vade_chi_so_gi_content):
    chunks = ck.chunk_track_a("vade-chi-so-gi.md", vade_chi_so_gi_content)
    assert chunks[-1]["section_heading"] == "Ghi chú đối chiếu — QUAN TRỌNG"


def test_track_a_header_metadata_extracted(vade_chi_so_gi_content):
    chunks = ck.chunk_track_a("vade-chi-so-gi.md", vade_chi_so_gi_content)
    assert chunks[0]["doc_title"] == "Chỉ số GI"
    assert chunks[0]["source_url"] == "https://vade.org.vn/chi-so-gi/"
    assert chunks[0]["crawl_date"] == "2026-08-07"


def test_track_a_chunk_id_deterministic_across_runs(vade_chi_so_gi_content):
    """chunk_id must not depend on ingested_at (wall-clock time) — otherwise
    every ingestion run would look like a full-corpus content change."""
    run1 = ck.chunk_track_a("vade-chi-so-gi.md", vade_chi_so_gi_content, ingested_at="2020-01-01T00:00:00Z")
    run2 = ck.chunk_track_a("vade-chi-so-gi.md", vade_chi_so_gi_content, ingested_at="2099-01-01T00:00:00Z")
    assert [c["chunk_id"] for c in run1] == [c["chunk_id"] for c in run2]
    assert [c["content_hash"] for c in run1] == [c["content_hash"] for c in run2]


def test_track_a_chunk_ids_globally_unique(vade_chi_so_gi_content):
    chunks = ck.chunk_track_a("vade-chi-so-gi.md", vade_chi_so_gi_content)
    ids = [c["chunk_id"] for c in chunks]
    assert len(ids) == len(set(ids))


def test_track_a_source_url_override_wins_over_header():
    content = "> Nguồn: https://in-file.example/\n> Crawl ngày: 2026-01-01\n\n# Title\n\nBody text.\n"
    chunks = ck.chunk_track_a("f.md", content, source_url="https://from-sources-csv.example/")
    assert chunks[0]["source_url"] == "https://from-sources-csv.example/"


# --- Track A: synthetic windowing/overlap check -----------------------------


def test_group_paragraphs_overlap_repeats_a_paragraph_across_windows():
    # 10 paragraphs, each ~100 tokens ("word " * 100), budget 250/overlap 60
    # forces multiple windows with a real carried-forward overlap.
    paragraphs = []
    pos = 0
    for i in range(10):
        text = f"paragraph {i} " + ("word " * 95)
        start = pos
        end = start + len(text)
        paragraphs.append((start, end, text))
        pos = end + 2
    groups = ck.group_paragraphs(paragraphs, max_tokens=250, overlap_tokens=60)
    assert len(groups) > 1
    # some paragraph tuple should appear in two consecutive groups (the overlap)
    first_group_paras = set(groups[0])
    second_group_paras = set(groups[1])
    assert first_group_paras & second_group_paras


def test_group_paragraphs_never_splits_a_single_oversized_paragraph():
    huge = ("word " * 2000, )[0]
    paragraphs = [(0, len(huge), huge)]
    groups = ck.group_paragraphs(paragraphs, max_tokens=100, overlap_tokens=10)
    assert len(groups) == 1
    assert groups[0] == paragraphs


# --- Track B: pseudo-header carry-forward (synthetic, deterministic) --------


def test_pseudo_header_carries_forward_until_next_match():
    content = (
        "PHẦN 1: MỞ ĐẦU\n\n"
        "Đoạn văn thuộc phần 1.\n\n"
        "Đoạn văn khác vẫn thuộc phần 1.\n\n"
        "PHẦN 2: NỘI DUNG CHÍNH\n\n"
        "Đoạn văn thuộc phần 2.\n"
    )
    headers = ck.find_pseudo_headers(content)
    assert [h[1] for h in headers] == ["PHẦN 1: MỞ ĐẦU", "PHẦN 2: NỘI DUNG CHÍNH"]
    # offset squarely inside the "phần 1" paragraphs -> heading is PHẦN 1
    mid_section_1 = content.index("Đoạn văn khác")
    assert ck._section_heading_at(headers, mid_section_1) == "PHẦN 1: MỞ ĐẦU"
    mid_section_2 = content.index("Đoạn văn thuộc phần 2")
    assert ck._section_heading_at(headers, mid_section_2) == "PHẦN 2: NỘI DUNG CHÍNH"
    assert ck._section_heading_at(headers, 0) is None or ck._section_heading_at(headers, -1) is None


def test_clean_heading_strips_toc_dot_leaders():
    line = "PHẦN 1: ĐỊNH NGHĨA VÀ DỊCH TỄ HỌC ........................................................... 8 "
    assert ck._clean_heading(line) == "PHẦN 1: ĐỊNH NGHĨA VÀ DỊCH TỄ HỌC"


def test_dense_numbered_run_is_dropped_as_a_reference_list():
    """A real header ('4. Chỉ định...') far from anything else must survive;
    a run of closely-packed numbered lines (a bibliography) must not."""
    matches = [
        (0, "4. Chỉ định đo đường huyết liên tục"),
        (5000, "1. American Diabetes Association."),
        (5040, "2. Consensus Statement by The American"),
        (5090, "3. Atlas IDF 2015"),
        (5110, "4. Current Medical Diagnosis and"),
    ]
    kept = ck._drop_dense_numbered_runs(matches)
    assert kept == [(0, "4. Chỉ định đo đường huyết liên tục")]


# --- Track B: real excerpt (byt-5481, Trang 20-21: Bảng 4 + Bảng 5) ---------


@pytest.fixture
def byt5481_table_excerpt() -> str:
    """Lines 765-868 of the real file: "--- Trang 20 ---" through the end of
    "--- Trang 21 ---"'s content, i.e. the "2. Mục tiêu điều trị" section
    containing Bảng 4 (adult glycemic targets) and Bảng 5 (older-adult
    targets) — the exact table §5.2 flags as high-stakes (the mock patient
    scenario in §7 depends on its HbA1c/glucose numbers). A representative
    slice of the 3,311-line file, not the whole thing, per the task brief."""
    full = (RAW_DIR / "byt-5481-qd-byt-full-text.md").read_text(encoding="utf-8")
    lines = full.splitlines(keepends=True)
    return "".join(lines[764:868])  # 0-indexed: lines 765..868 inclusive


def test_byt5481_excerpt_has_expected_page_markers(byt5481_table_excerpt):
    markers = ck.find_page_markers(byt5481_table_excerpt)
    assert [page for _, page in markers] == [20, 21]


def test_byt5481_excerpt_chunks_have_valid_offsets(byt5481_table_excerpt):
    chunks = ck.chunk_track_b("byt-5481-qd-byt-full-text.md", byt5481_table_excerpt)
    assert chunks
    for c in chunks:
        assert byt5481_table_excerpt[c["char_start"] : c["char_end"]] == c["text"]


def test_byt5481_bang4_table_stays_atomic_with_its_numbers(byt5481_table_excerpt):
    """The specific correctness property §5.2 warns about: the HbA1c/glucose
    target numbers must land in the same chunk as the "Bảng 4:" label that
    identifies what table they belong to — a table cut in half here would
    silently produce an uncitable or mis-attributed number."""
    chunks = ck.chunk_track_b("byt-5481-qd-byt-full-text.md", byt5481_table_excerpt)
    bang4_chunks = [c for c in chunks if "Bảng 4:" in c["text"]]
    assert len(bang4_chunks) == 1
    text = bang4_chunks[0]["text"]
    assert "HbA1c" in text
    assert "7%" in text
    assert "80-130" in text  # fasting glucose target range


def test_byt5481_excerpt_page_numbers_assigned(byt5481_table_excerpt):
    chunks = ck.chunk_track_b("byt-5481-qd-byt-full-text.md", byt5481_table_excerpt)
    pages = {c["page_number"] for c in chunks}
    assert pages <= {20, 21}
    assert pages  # at least one chunk got a page number


def test_byt5481_excerpt_section_heading_picked_up(byt5481_table_excerpt):
    chunks = ck.chunk_track_b("byt-5481-qd-byt-full-text.md", byt5481_table_excerpt)
    headings = {c["section_heading"] for c in chunks}
    assert "2. Mục tiêu điều trị" in headings


# --- Track B: whole-corpus dry run (no embeddings, offsets + no exceptions) -


def test_all_track_b_files_chunk_without_error_and_have_valid_offsets():
    for filename in sorted(ck.TRACK_B_FILES):
        content = (RAW_DIR / filename).read_text(encoding="utf-8")
        chunks = ck.chunk_track_b(filename, content)
        assert chunks, f"{filename} produced zero chunks"
        for c in chunks:
            assert content[c["char_start"] : c["char_end"]] == c["text"], f"{filename} chunk {c['chunk_index']}"
            assert c["text"].strip()


def test_byt5481_and_byt3319_page_marker_presence_matches_ground_truth():
    """Regression guard for a discrepancy found while building this pipeline:
    the design doc's §5.2 prose says byt-5481 has *no* page markers and
    implies byt-3319 does. Direct inspection of the actual files shows the
    opposite (byt-5481: 77 markers; byt-3319: 0). chunk_track_b must detect
    this from each file itself, not from a hardcoded assumption either way."""
    byt5481 = (RAW_DIR / "byt-5481-qd-byt-full-text.md").read_text(encoding="utf-8")
    byt3319 = (RAW_DIR / "byt-3319-qd-byt-full-text.md").read_text(encoding="utf-8")
    assert ck.find_page_markers(byt5481)
    assert not ck.find_page_markers(byt3319)

    b3319_chunks = ck.chunk_track_b("byt-3319-qd-byt-full-text.md", byt3319)
    assert all(c["page_number"] is None for c in b3319_chunks)


def test_all_track_a_files_chunk_without_error_and_have_valid_offsets():
    for filename in sorted(ck.TRACK_A_FILES):
        content = (RAW_DIR / filename).read_text(encoding="utf-8")
        chunks = ck.chunk_track_a(filename, content)
        assert chunks, f"{filename} produced zero chunks"
        for c in chunks:
            assert content[c["char_start"] : c["char_end"]] == c["text"], f"{filename} chunk {c['chunk_index']}"
            assert c["text"].strip()


def test_whole_corpus_chunk_ids_globally_unique():
    all_ids = []
    for filename in sorted(ck.TRACK_A_FILES | ck.TRACK_B_FILES):
        content = (RAW_DIR / filename).read_text(encoding="utf-8")
        chunks = ck.chunk_tier2_file(filename, content)
        all_ids.extend(c["chunk_id"] for c in chunks)
    assert len(all_ids) == len(set(all_ids))
    assert len(all_ids) > 500  # sanity bound, not an exact pin to one algorithm tuning


# --- has_curator_annotation ---------------------------------------------------


def test_curator_bracket_annotations_are_kept_not_stripped():
    """§5.2: bracketed curator notes like "[Lưu ý: ...]" and "[Chuyển đổi
    ảnh → text...]" are intentional content, not extraction noise — they
    must remain verbatim in the chunk text, with has_curator_annotation
    flagging that the chunk needs to be cited as curator-transcribed rather
    than verbatim guideline prose."""
    content = (
        "--- Trang 1 ---\n"
        "Nội dung bình thường.\n\n"
        "[Lưu ý: tên người ký bị lỗi encoding font ngay trong PDF gốc.]\n\n"
        "> **[Chuyển đổi ảnh → text để phục vụ embedding — nguồn ảnh: images/x.png]**\n"
    )
    chunks = ck.chunk_track_b("byt-5481-qd-byt-full-text.md", content)
    assert any(c["has_curator_annotation"] for c in chunks)
    annotated = [c for c in chunks if c["has_curator_annotation"]]
    assert any("[Lưu ý" in c["text"] or "[Chuyển đổi ảnh" in c["text"] for c in annotated)


# --- Tier 1 (sources.csv) ----------------------------------------------------


@pytest.fixture
def sources_rows() -> list[dict[str, str]]:
    with SOURCES_CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_tier1_skip_classification_matches_verified_corpus_counts(sources_rows):
    """Locks in the real counts verified by hand while building this
    pipeline: 56 rows total, 3 unembeddable (1 no_local_file / deprioritized
    duplicate, 2 explicitly flagged as failed 403-blocked crawls), 53
    embeddable. (The design doc's §5.1 prose approximates this as "~3 rows
    with no local_file" and "~4 rows flagged as failed crawl" as if they
    were disjoint sets summing to 7 skips — they are not: every failed-crawl
    row is also a no-local_file row in this corpus, so both descriptions
    point at the same 3 rows viewed two ways, not 7 distinct rows.)"""
    reasons = [ck.classify_tier1_skip(row) for row in sources_rows]
    no_local = reasons.count(ck.SKIP_NO_LOCAL_FILE)
    failed_crawl = reasons.count(ck.SKIP_FAILED_CRAWL)
    embedded = reasons.count(None)
    assert (no_local, failed_crawl, embedded) == (1, 2, 53)
    assert no_local + failed_crawl + embedded == len(sources_rows) == 56


def test_build_tier1_chunk_embeds_key_content_verbatim(sources_rows):
    row = sources_rows[1]  # Bảng 4 row
    chunk = ck.build_tier1_chunk(row, 1)
    assert chunk["text"] == row["key_content"].strip()
    assert chunk["tier"] == 1
    assert chunk["trust_tier"] == "Bộ Y tế"
    assert chunk["page_ref"] == "tr. 19"
    assert chunk["content_hash"] == ck.compute_hash(row["key_content"].strip())


def test_build_tier1_chunk_id_depends_on_row_id_and_content(sources_rows):
    row = sources_rows[1]
    a = ck.build_tier1_chunk(row, row_id=1)
    b = ck.build_tier1_chunk(row, row_id=2)  # same content, different row_id
    assert a["chunk_id"] != b["chunk_id"]


def test_tier1_skip_reason_no_local_file_and_low_priority_note(sources_rows):
    row = next(r for r in sources_rows if "trùng lặp cao" in r["notes"])
    assert ck.classify_tier1_skip(row) == ck.SKIP_NO_LOCAL_FILE


def test_tier1_skip_reason_failed_crawl(sources_rows):
    row = next(r for r in sources_rows if "THẤT BẠI" in r["notes"])
    assert ck.classify_tier1_skip(row) == ck.SKIP_FAILED_CRAWL
