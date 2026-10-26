"""Unit tests for codebase/models/rag/chunking.py (v2, structure-aware).

Pure string-in/dict-out logic, no model inference and no vector store. Small
synthetic inputs pin each cleaning/segmentation rule; whole-corpus tests pin
the regressions the v1 chunker had on the real files (see the module
docstring of chunking.py for the measured v1 numbers).
"""

from __future__ import annotations

import collections
import csv
import re
from pathlib import Path

import pytest

from codebase.models.rag import chunking as ck

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "knowledge_base" / "raw"
SOURCES_CSV = REPO_ROOT / "data" / "knowledge_base" / "sources.csv"


@pytest.fixture(scope="module")
def corpus() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(RAW_DIR.glob("*.md"))}


@pytest.fixture(scope="module")
def vocab(corpus) -> ck.SyllableVocab:
    return ck.build_syllable_vocab([c for c in corpus.values() if "--- Trang " not in c])


@pytest.fixture(scope="module")
def chunks_by_file(corpus, vocab) -> dict[str, list[dict]]:
    return {name: ck.chunk_document(name, content, syllable_vocab=vocab) for name, content in corpus.items()}


# --- split-syllable repair ----------------------------------------------------

_VOCAB = {"thuốc": 20, "đồng": 10, "vận": 5, "sau": 50, "ăn": 40, "hạ": 30, "h": 3, "ạ": 1, "của": 90}


def test_repair_joins_fragments_that_are_not_words():
    text, n = ck.repair_split_syllables("thu ốc đ ồng v ận", _VOCAB)
    assert text == "thuốc đồng vận"
    assert n == 3


def test_repair_keeps_two_real_words_whose_concatenation_is_not_a_word():
    assert ck.repair_split_syllables("sau ăn", _VOCAB) == ("sau ăn", 0)


def test_repair_joins_two_real_words_when_the_joined_word_dominates():
    # "h" (giờ) and "ạ" are both words, but "hạ" is 10x more frequent.
    assert ck.repair_split_syllables("h ạ đường huyết", _VOCAB)[0] == "hạ đường huyết"


def test_repair_handles_overlapping_pairs_and_punctuation():
    # The pair to fix is ("c", "ủa,"), after a word that must stay alone.
    assert ck.repair_split_syllables("sau c ủa, ăn", _VOCAB)[0] == "sau của, ăn"


def test_repair_never_changes_clean_web_files(corpus, vocab):
    """False-positive guard: the clean files are where the vocabulary comes
    from, so the repair must be a no-op on them."""
    for name, content in corpus.items():
        if "--- Trang " not in content:
            assert ck.repair_split_syllables(content, vocab)[1] == 0, name


# --- PDF cleaning ---------------------------------------------------------------


def _pdf(*pages: list[str]) -> str:
    return "\n".join(f"--- Trang {i} ---\n" + "\n".join(lines) for i, lines in enumerate(pages, 1))


def test_clean_drops_toc_pages_page_numbers_and_running_headers():
    header = "HƯỚNG DẪN CỦA IWGDF VỀ GIẢM TẢI"
    toc = ["MỤC LỤC", "1. Mở đầu ........ 3", "2. Điều trị ........ 5", "3. Theo dõi ........ 9"]
    topics = ["giảm tải", "giày dép", "nẹp cổ chân", "theo dõi áp lực", "tái khám"]
    body = [[header, f"{n}", f"Nội dung về {topic}."] for n, topic in enumerate(topics, 2)]
    lines, _ = ck.parse_lines(_pdf(toc, *body))
    kept, chapters, stats = ck.clean_pdf_lines(lines, None)
    texts = [ln.text for ln in kept]
    assert stats.toc_pages_dropped == 1
    assert not any("........" in t for t in texts)
    assert header not in texts
    assert not any(t.strip().isdigit() for t in texts)
    assert "Nội dung về nẹp cổ chân." in texts
    assert set(chapters.values()) == {header}


def test_alternating_running_header_phrasings_keep_one_chapter():
    a, b = "HƯỚNG DẪN GIẢM TẢI CỦA IWGDF", "HƯỚNG DẪN CỦA IWGDF VỀ GIẢM TẢI"
    pages = [[a if n % 2 else b, f"Đoạn {n}."] for n in range(1, 9)]
    lines, _ = ck.parse_lines(_pdf(*pages))
    _, chapters, _ = ck.clean_pdf_lines(lines, None)
    assert len(set(chapters.values())) == 1


# --- heading detection ----------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "rank"),
    [
        ("PHẦN 2: CHẨN ĐOÁN VÀ PHÂN LOẠI", 1),
        ("PHỤ LỤC 03:", 1),
        ("IV. ĐIỀU TRỊ", 1),
        ("1. Chẩn đoán", 2),
        ("1.1. Chẩn đoán đái tháo đường", 3),
        ("3.3.2.4. Chụp cắt lớp vi tính mạch máu (CLVT)", 5),
        ("2.5 mg mỗi ngày", None),  # body text, not a heading
        ("1. Tăng glucose máu;", None),  # list item
        ("CHẨN ĐOÁN", 2),  # IWGDF section title
        ("IWGDF", None),  # single ALL-CAPS word: too weak
        ("ABI, CK, CKMB, BNP,", None),  # a table cell that wrapped, not a title
    ],
)
def test_heading_rank_pdf(line, rank):
    assert ck._heading_rank(line, pdf=True) == rank


def test_numbered_recommendation_out_of_sequence_is_not_a_heading():
    """v1 read IWGDF's numbered recommendation "27. Để điều trị ..." as a
    section heading and carried it across 77 chunks."""
    lines, _ = ck.parse_lines(
        "--- Trang 1 ---\n1. Chẩn đoán\nVăn bản.\n2. Điều trị\nVăn bản.\n"
        "27. Để điều trị chuyên biệt nhiễm trùng trong loét chân đái tháo đường:\nVăn bản khuyến cáo.\n"
    )
    headings = [s.text for s in ck.segment_lines(lines, pdf=True) if s.kind == "heading"]
    assert headings == ["1. Chẩn đoán", "2. Điều trị"]


def test_phan_heading_takes_its_title_from_the_next_line():
    lines, _ = ck.parse_lines("--- Trang 1 ---\nPHẦN 6\n\nHẠ ĐƯỜNG HUYẾT\nHạ glucose huyết là biến chứng cấp tính.\n")
    headings = [s.text for s in ck.segment_lines(lines, pdf=True) if s.kind == "heading"]
    assert headings == ["PHẦN 6: HẠ ĐƯỜNG HUYẾT"]


def test_decimal_heading_does_not_nest_under_an_unrelated_numbered_section():
    """QĐ 5481: "4." and "5." were lost to PDF line breaks, so "5.2.3. Dinh
    dưỡng" landed under "3. Phân tầng nguy cơ" in v2.0."""
    segs = [
        ck.Segment("heading", "PHẦN 4: ĐIỀU TRỊ", 1, 1, rank=1),
        ck.Segment("heading", "3. Phân tầng nguy cơ", 1, 1, rank=2),
        ck.Segment("heading", "4.2. Lựa chọn cụ thể", 1, 1, rank=3),
        ck.Segment("heading", "5.2.3. Dinh dưỡng", 2, 2, rank=4),
        ck.Segment("prose", "Glucid chiếm 50-60% năng lượng.", 2, 2),
    ]
    sections, _ = ck.build_sections(segs, {})
    assert sections[-1].path == ["PHẦN 4: ĐIỀU TRỊ", "5.2.3. Dinh dưỡng"]


def test_pdf_hard_wrapped_lines_are_rejoined_into_one_paragraph():
    lines, _ = ck.parse_lines(
        "--- Trang 1 ---\nĐái tháo đường là bệnh mạn tính\nảnh hưởng tới nhiều cơ quan.\nCâu thứ hai bắt đầu.\n"
    )
    prose = [s.text for s in ck.segment_lines(lines, pdf=True) if s.kind == "prose"]
    assert prose == ["Đái tháo đường là bệnh mạn tính ảnh hưởng tới nhiều cơ quan.", "Câu thứ hai bắt đầu."]


def test_wrapped_bold_heading_in_html_file_is_one_heading():
    lines, _ = ck.parse_lines("**4. Tiêu chuẩn để làm xét nghiệm chẩn\nđoán ĐTĐ**\n\nNội dung.\n")
    headings = [s.text for s in ck.segment_lines(lines, pdf=False) if s.kind == "heading"]
    assert headings == ["4. Tiêu chuẩn để làm xét nghiệm chẩn đoán ĐTĐ"]


# --- chunk_document on synthetic and real excerpts ---------------------------------


def test_chunk_text_starts_with_title_and_heading_path():
    content = "# Metformin — Dược thư\n\n## Chống chỉ định\n\nSuy thận nặng (eGFR < 30).\n"
    (chunk,) = ck.chunk_document("duocthu-metformin-monograph.md", content)
    assert chunk["text"].startswith("Metformin — Dược thư\nChống chỉ định\n\n")
    assert chunk["heading_path"] == "Chống chỉ định"
    assert chunk["doc_type"] == "drug_monograph"


def test_reference_sections_are_dropped():
    content = (
        "# Tài liệu\n\n## Điều trị\n\nNội dung điều trị.\n\n## Tài liệu tham khảo\n\n"
        "1. Tác giả A. Bài báo.\n2. Tác giả B. Bài báo khác.\n"
    )
    chunks = ck.chunk_document("vade-x.md", content)
    assert all("Tác giả A" not in c["text"] for c in chunks)


def test_curator_annotations_are_kept_and_flagged():
    content = (
        "--- Trang 1 ---\nNội dung bình thường.\n\n"
        "[Lưu ý: tên người ký bị lỗi encoding font ngay trong PDF gốc.]\n\n"
        "> **[Chuyển đổi ảnh → text để phục vụ embedding — nguồn ảnh: images/x.png]**\n"
    )
    chunks = ck.chunk_document("byt-5481-qd-byt-full-text.md", content)
    annotated = [c for c in chunks if c["has_curator_annotation"]]
    assert annotated
    assert any("[Lưu ý" in c["text"] or "[Chuyển đổi ảnh" in c["text"] for c in annotated)


@pytest.fixture
def byt5481_table_excerpt(corpus) -> str:
    """ "--- Trang 20 ---" through page 21: the "2. Mục tiêu điều trị" section
    with Bảng 4 (adult targets) and Bảng 5 (older adults) — the numbers the
    mock patient scenario depends on."""
    full = corpus["byt-5481-qd-byt-full-text.md"]
    start = full.index("--- Trang 20 ---")
    end = full.index("--- Trang 22 ---")
    return full[start:end]


def test_byt5481_bang4_table_stays_together_with_its_numbers(byt5481_table_excerpt):
    chunks = ck.chunk_document("byt-5481-qd-byt-full-text.md", byt5481_table_excerpt)
    bang4 = [c for c in chunks if "Bảng 4:" in c["text"]]
    assert len(bang4) == 1
    text = bang4[0]["text"]
    assert "HbA1c" in text and "7%" in text and "80-130" in text
    assert {c["page_start"] for c in chunks} <= {20, 21}
    assert any("2. Mục tiêu điều trị" in (c["heading_path"] or "") for c in chunks)


# --- whole corpus ---------------------------------------------------------------------


def test_every_corpus_file_chunks_and_ids_are_unique(chunks_by_file):
    ids = [c["chunk_id"] for chunks in chunks_by_file.values() for c in chunks]
    assert all(chunks_by_file.values())
    assert len(ids) == len(set(ids))


def test_chunk_ids_are_deterministic(corpus, vocab):
    name = "byt-3280-qd-byt-full-text.md"
    a = ck.chunk_document(name, corpus[name], syllable_vocab=vocab, ingested_at="x")
    b = ck.chunk_document(name, corpus[name], syllable_vocab=vocab, ingested_at="y")
    assert [c["chunk_id"] for c in a] == [c["chunk_id"] for c in b]


def test_chunks_stay_within_budget(chunks_by_file):
    """v1: 234 of 784 long-document chunks ran over 1,000 tokens. Now only a
    table may exceed MAX_TOKENS, and never by more than the context line."""
    for chunks in chunks_by_file.values():
        for c in chunks:
            assert c["n_tokens"] <= ck.TABLE_MAX_TOKENS + 128, (c["source_file"], c["chunk_index"])
    all_chunks = [c for chunks in chunks_by_file.values() for c in chunks]
    over = sum(c["n_tokens"] > ck.MAX_TOKENS for c in all_chunks)
    assert over / len(all_chunks) < 0.03


def test_no_page_number_or_toc_only_chunks(chunks_by_file):
    for chunks in chunks_by_file.values():
        for c in chunks:
            body = c["text"].split("\n\n", 1)[-1]
            assert not re.fullmatch(r"\s*\d{1,3}\s*", body)
            assert not re.search(r"\.{8,}\s*\d+\s*$", body, re.MULTILINE), (c["source_file"], body[:80])


def test_byt3319_bold_headings_are_detected(chunks_by_file):
    """v1 found no headings at all in 3319 (they are bold, not `##`)."""
    chunks = chunks_by_file["byt-3319-qd-byt-full-text.md"]
    with_path = [c for c in chunks if c["heading_path"]]
    assert len(with_path) / len(chunks) > 0.9
    assert any(c["heading_path"].startswith("II. CHẨN ĐOÁN") for c in with_path)


def test_iwgdf_heading_paths_are_not_carried_across_the_whole_file(chunks_by_file):
    """v1 carried one heading across 142 IWGDF chunks; now each chunk sits
    under its sub-guideline (running header) and a section within it."""
    chunks = chunks_by_file["iwgdf-2019-guideline-full-text.md"]
    most_common = collections.Counter(c["heading_path"] for c in chunks).most_common(1)[0][1]
    assert most_common < 40
    assert not any("27. Để điều trị" in (c["heading_path"] or "") for c in chunks)
    tops = {(c["heading_path"] or "").split(ck.CONTEXT_SEPARATOR)[0] for c in chunks}
    assert "HƯỚNG DẪN CHẨN ĐOÁN VÀ ĐIỀU TRỊ NHIỄM TRÙNG" in tops


def test_byt5481_split_syllables_are_repaired(chunks_by_file):
    text = "\n".join(c["text"] for c in chunks_by_file["byt-5481-qd-byt-full-text.md"])
    for broken in ("c ủa", "li ệu", "g ồm", "thu ốc"):
        assert broken not in text


def test_reference_lists_are_not_indexed(chunks_by_file):
    for chunks in chunks_by_file.values():
        for c in chunks:
            assert "TÀI LIỆU THAM KHẢO" not in (c["heading_path"] or "").upper()


def test_superseded_decisions_are_marked(chunks_by_file):
    for name in ("byt-3280-qd-byt-full-text.md", "byt-3319-qd-byt-full-text.md"):
        chunks = chunks_by_file[name]
        assert {c["status"] for c in chunks} == {"superseded"}
        assert all("[VĂN BẢN ĐÃ HẾT HIỆU LỰC]" in c["text"].split("\n", 1)[0] for c in chunks)
    assert {c["status"] for c in chunks_by_file["byt-5481-qd-byt-full-text.md"]} == {"active"}


def test_drug_monograph_chunks_name_the_drug(chunks_by_file):
    """v1 embedded "## Chống chỉ định" sections with no drug name in the text."""
    for name, chunks in chunks_by_file.items():
        if name.startswith("duocthu-"):
            drug = name.removeprefix("duocthu-").removesuffix("-monograph.md")
            assert all(drug.lower() in c["text"].split("\n", 1)[0].lower() for c in chunks), name


def test_every_file_has_a_known_doc_type(corpus):
    assert {name for name in corpus if ck.doc_type_for(name) == "other"} == set()


# --- Tier 1 (sources.csv) ----------------------------------------------------


@pytest.fixture
def sources_rows() -> list[dict[str, str]]:
    with SOURCES_CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_tier1_skip_classification_matches_verified_corpus_counts(sources_rows):
    """56 rows: 1 with no local_file (a deprioritized duplicate), 2 flagged
    as failed (403-blocked) crawls, 53 embeddable."""
    reasons = [ck.classify_tier1_skip(row) for row in sources_rows]
    assert (reasons.count(ck.SKIP_NO_LOCAL_FILE), reasons.count(ck.SKIP_FAILED_CRAWL), reasons.count(None)) == (
        1,
        2,
        53,
    )


def test_build_tier1_chunk_prefixes_title_and_keeps_key_content(sources_rows):
    row = sources_rows[1]  # Bảng 4 row
    chunk = ck.build_tier1_chunk(row, 1)
    assert chunk["text"] == f"{row['document_title'].strip()}\n\n{row['key_content'].strip()}"
    assert chunk["tier"] == 1
    assert chunk["trust_tier"] == "Bộ Y tế"
    assert chunk["page_ref"] == "tr. 19"
    assert chunk["content_hash"] == ck.compute_hash(chunk["text"], ck.CHUNKER_VERSION)


def test_tier1_chunk_id_is_unchanged_from_v1(sources_rows):
    """The retrieval-recall eval's gold labels are tier-1 chunk_ids, so the id
    must stay derived from (row_id, key_content) only — not the new text."""
    row = sources_rows[1]
    expected = ck.make_chunk_id("t1", "sources_csv", "1", row["key_content"].strip())
    assert ck.build_tier1_chunk(row, 1)["chunk_id"] == expected
    assert ck.build_tier1_chunk(row, 2)["chunk_id"] != expected


def test_tier1_superseded_status_is_shown_in_text(sources_rows):
    row = sources_rows[1]
    chunk = ck.build_tier1_chunk(row, 1, status="superseded")
    assert chunk["status"] == "superseded"
    assert "[VĂN BẢN ĐÃ HẾT HIỆU LỰC]" in chunk["text"].split("\n", 1)[0]


def test_tier1_skip_reason_no_local_file_and_low_priority_note(sources_rows):
    row = next(r for r in sources_rows if "trùng lặp cao" in r["notes"])
    assert ck.classify_tier1_skip(row) == ck.SKIP_NO_LOCAL_FILE


def test_tier1_skip_reason_failed_crawl(sources_rows):
    row = next(r for r in sources_rows if "THẤT BẠI" in r["notes"])
    assert ck.classify_tier1_skip(row) == ck.SKIP_FAILED_CRAWL
