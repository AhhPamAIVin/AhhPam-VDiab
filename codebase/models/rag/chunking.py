"""Knowledge-base chunking: raw/*.md and sources.csv rows -> chunk dicts.

OWNER: RAG data-pipeline workstream. Do not edit from other workstreams.

Pure string-in, dict-out — no vector store or model inference here
(codebase/models/rag/ingest.py owns all I/O); the only optional dependency is
the embedding model's tokenizer, used to measure chunk budgets.

v2 (CHUNKER_VERSION 2.x) replaces the v1 "Track A / Track B" split with one
structure-aware chunker for every file. Measured on the v1 output: 30% of
long-document chunks ran over 1,000 tokens (a PDF page with single newlines
was one unsplittable "paragraph"), 127 chunks were page numbers or TOC
fragments, QĐ 3319 had no section headings at all (its headings are bold, not
`##`), IWGDF carried one wrong heading across 142 chunks (numbered
recommendations "27. Để điều trị..." read as headings), and QĐ 5481 had ~1,400
PDF-split syllables ("c ủa", "li ệu"). Pipeline per file:

1. Clean (PDF-extracted files, i.e. those with `--- Trang N ---` markers):
   drop table-of-contents pages and page-number lines, strip running
   headers/footers (kept as the page's chapter label), re-join syllables the
   PDF extraction split, using a vocabulary built from the clean web files.
2. Segment lines into headings, prose paragraphs (hard-wrapped lines
   re-joined), list items, tables (markdown `|` rows, or a `Bảng N:` caption
   and the rows under it) and curator blockquotes, each with its pages.
3. Build the heading tree (`#`, PHẦN/CHƯƠNG, Roman numerals, 1. / 1.1. /
   1.1.1., bold lines, short ALL-CAPS lines) and cut per section; a section
   over budget is split on segment, then sentence (or table-row) boundaries.
   Tiny neighbouring chunks under the same chapter are merged. Bibliography,
   TOC and acknowledgement sections are dropped.
4. Each chunk's text starts with a context line — document title and heading
   path — so the embedding (and the LLM reading it) knows where an excerpt
   like "Chống chỉ định: ..." comes from.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

CHUNKER_VERSION = "2.0.0"
CORPUS_VERSION = "2.0.0"

# Budgets in embedding-model tokens (bge-m3's XLM-R tokenizer: ~3.5 chars per
# token on this corpus, vs ~2.1 for OpenAI's cl100k — so 384 here is roughly
# the same text as v1's 800 cl100k tokens would have been at ~650).
TARGET_TOKENS = 384
MAX_TOKENS = 512
MIN_TOKENS = 64
OVERLAP_TOKENS = 48
# A table is kept whole up to this size before it is split by rows.
TABLE_MAX_TOKENS = 768

DEFAULT_TOKENIZER_MODEL = "BAAI/bge-m3"
CONTEXT_SEPARATOR = " › "


# --- token counting ------------------------------------------------------------

_TOKENIZER: Any = None
_TOKENIZER_LOADED = False
_CHARS_PER_TOKEN = 3.5


def _tokenizer_model() -> str:
    try:
        from codebase.backend.config import get_settings

        return get_settings().embedding_model
    except Exception:
        return DEFAULT_TOKENIZER_MODEL


def _get_tokenizer() -> Any:
    global _TOKENIZER, _TOKENIZER_LOADED
    if not _TOKENIZER_LOADED:
        _TOKENIZER_LOADED = True
        try:
            from codebase.models.rag.embeddings import get_tokenizer

            _TOKENIZER = get_tokenizer(_tokenizer_model())
        except Exception:
            _TOKENIZER = None
    return _TOKENIZER


def estimate_tokens(text: str) -> int:
    """Token count under the embedding model's tokenizer, or a chars/3.5
    estimate (calibrated on this corpus) when the tokenizer isn't available."""
    tokenizer = _get_tokenizer()
    if tokenizer is not None:
        try:
            return len(tokenizer(text, add_special_tokens=False)["input_ids"])
        except Exception:
            pass
    return max(1, round(len(text) / _CHARS_PER_TOKEN))


# --- shared helpers ------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def compute_hash(*parts: str) -> str:
    """Full sha256 hex digest over `parts`, joined with a separator that
    won't appear in real content."""
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def make_chunk_id(prefix: str, *parts: str) -> str:
    return f"{prefix}-{compute_hash(*parts)[:16]}"


_HEADER_LINE_RE = re.compile(r"^>\s?(.*)$")
_SOURCE_URL_RE = re.compile(r"Nguồn(?: tệp gốc)?:\s*(\S.*)$")
_CRAWL_DATE_RE = re.compile(r"(?:Crawl ngày|Trích xuất ngày):\s*(\S+)")


def parse_file_header(content: str) -> tuple[str | None, str | None, int]:
    """Parse the leading `> Nguồn: ...` / `> Crawl ngày: ...` blockquote most
    raw/*.md files open with. Returns (source_url, crawl_date,
    body_start_offset); (None, None, 0) when there is no header (byt-5481)."""
    source_url: str | None = None
    crawl_date: str | None = None
    offset = 0
    for line in content.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        if not stripped.strip():
            offset += len(line)
            continue
        m = _HEADER_LINE_RE.match(stripped)
        if not m:
            break
        body = m.group(1)
        if url_match := _SOURCE_URL_RE.search(body):
            source_url = url_match.group(1).strip()
        if date_match := _CRAWL_DATE_RE.search(body):
            crawl_date = date_match.group(1).strip()
        offset += len(line)
    return source_url, crawl_date, offset


_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_QUOTED_TITLE_RE = re.compile(r'tài liệu chuyên môn\s*["“]([^"”]+)["”]')


def extract_doc_title(content: str) -> str | None:
    """First H1 line, verbatim. Falls back to the curator's own quoted
    "tài liệu chuyên môn "X"" phrasing for the one file with no H1 (byt-5481)."""
    if h1 := _H1_RE.search(content):
        return h1.group(1).strip()
    if quoted := _QUOTED_TITLE_RE.search(content):
        return quoted.group(1).strip()
    return None


_SUPERSEDED_RE = re.compile(r"\(SUPERSEDED\)|VĂN BẢN ĐÃ HẾT HIỆU LỰC")


def detect_status(content: str) -> str:
    """'superseded' when the curator marked the document as replaced (3280,
    3319 carry a "VĂN BẢN ĐÃ HẾT HIỆU LỰC (SUPERSEDED)" section), else 'active'."""
    return "superseded" if _SUPERSEDED_RE.search(content) else "active"


# Document type by filename prefix — explicit, so a new source family gets a
# deliberate label rather than a guess. Unknown prefixes map to "other".
DOC_TYPE_BY_PREFIX: dict[str, str] = {
    "byt-": "national_guideline",
    "iwgdf-": "international_guideline",
    "ada-": "international_guideline_summary",
    "who-": "international_guideline_summary",
    "duocthu-": "drug_monograph",
    "vinmec-": "patient_education",
    "vade-": "patient_education",
    "medinet-": "patient_education",
    "vncdc-": "patient_education",
    "bachmai-": "patient_education",
    "benhviennoitiet-": "patient_education",
    "viendinhduong-": "food_reference",
}


def doc_type_for(filename: str) -> str:
    for prefix, doc_type in DOC_TYPE_BY_PREFIX.items():
        if filename.startswith(prefix):
            return doc_type
    return "other"


# --- step 1: cleaning PDF-extracted text -----------------------------------------

_PAGE_MARKER_RE = re.compile(r"^--- Trang (\d+) ---\s*$")
_TOC_LEADER_RE = re.compile(r"\.{5,}\s*\d+\s*$")
_PAGE_NUMBER_RE = re.compile(r"^\d{1,3}$")
_WORD_RE = re.compile(r"\w+", re.UNICODE)
_TRAILING_LETTERS_RE = re.compile(r"([^\W\d_]+)$")
_LEADING_LETTERS_RE = re.compile(r"^([^\W\d_]+)")


@dataclass
class Line:
    text: str
    page: int | None


SyllableVocab = dict[str, int]

# A pair whose halves are both real words ("h" = giờ, "ạ") is still a split
# syllable when the joined word is this many times more frequent than the
# rarer half ("hạ" in "hạ đường huyết").
_SPLIT_FREQUENCY_RATIO = 4


def build_syllable_vocab(texts: list[str]) -> SyllableVocab:
    """Lower-cased word counts from text known to be cleanly extracted (the
    web files, which have no PDF page markers) — the reference for deciding
    whether a "c ủa"-style pair is a split syllable or two real words."""
    counts: Counter[str] = Counter()
    for text in texts:
        counts.update(w.lower() for w in _WORD_RE.findall(text) if not w.isdigit())
    return dict(counts)


def repair_split_syllables(text: str, vocab: SyllableVocab) -> tuple[str, int]:
    """Re-join `A B` into `AB` when AB is a known word and A or B is not
    ("li ệu" -> "liệu", "chẩ n" -> "chẩn"), or both are but AB is far more
    common ("h ạ" -> "hạ", "u ống" -> "uống"). "sau ăn" stays: "sauăn" is not
    a word. Returns the repaired text and how many joins were made."""
    if not vocab:
        return text, 0
    count = 0

    def joinable(left: str, right: str) -> bool:
        a = _TRAILING_LETTERS_RE.search(left)
        b = _LEADING_LETTERS_RE.match(right)
        if not a or not b:
            return False
        wa, wb = a.group(1).lower(), b.group(1).lower()
        joined = vocab.get(wa + wb, 0)
        if not joined:
            return False
        rarer = min(vocab.get(wa, 0), vocab.get(wb, 0))
        return rarer == 0 or rarer * _SPLIT_FREQUENCY_RATIO <= joined

    # Token walk rather than one regex pass, so overlapping pairs are all
    # seen: in "phát hi ện" the pair to fix is ("hi", "ện"), not ("phát", "hi").
    parts = text.split(" ")
    out: list[str] = []
    i = 0
    while i < len(parts):
        current = parts[i]
        while i + 1 < len(parts) and joinable(current, parts[i + 1]):
            current += parts[i + 1]
            i += 1
            count += 1
        out.append(current)
        i += 1
    return " ".join(out), count


def _norm_key(line: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d+", "", line)).strip().lower()


def _jaccard(a: str, b: str) -> float:
    sa, sb = set(a.split()), set(b.split())
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


@dataclass
class CleanStats:
    toc_pages_dropped: int = 0
    page_number_lines_dropped: int = 0
    running_header_lines_dropped: int = 0
    syllables_repaired: int = 0


def clean_pdf_lines(lines: list[Line], vocab: SyllableVocab | None) -> tuple[list[Line], dict[int, str], CleanStats]:
    """Drop TOC pages, page-number lines and running headers/footers; return
    the kept lines, a page -> chapter-label map (from running headers, e.g.
    IWGDF's "HƯỚNG DẪN CHẨN ĐOÁN VÀ ĐIỀU TRỊ NHIỄM TRÙNG" atop every page of
    that sub-guideline) and what was removed."""
    stats = CleanStats()
    pages: dict[int | None, list[int]] = {}
    for idx, line in enumerate(lines):
        pages.setdefault(line.page, []).append(idx)

    drop: set[int] = set()
    edge_keys: Counter[str] = Counter()
    page_edges: dict[int | None, list[int]] = {}
    for page, idxs in pages.items():
        non_blank = [i for i in idxs if lines[i].text.strip()]
        if not non_blank:
            continue
        leaders = sum(1 for i in non_blank if _TOC_LEADER_RE.search(lines[i].text))
        if leaders >= max(3, 0.3 * len(non_blank)):
            drop.update(idxs)
            stats.toc_pages_dropped += 1
            continue
        edges = non_blank[:3] + non_blank[-2:]
        page_edges[page] = edges
        for key in {_norm_key(lines[i].text) for i in edges}:
            if len(key) >= 8 and len(key.split()) >= 2:
                edge_keys[key] += 1

    min_repeat = max(3, int(0.05 * len(pages)))
    running = {key for key, n in edge_keys.items() if n >= min_repeat}
    chapter_by_page: dict[int, str] = {}
    for page, edges in page_edges.items():
        for i in edges:
            text = lines[i].text.strip()
            if _PAGE_NUMBER_RE.match(text):
                drop.add(i)
                stats.page_number_lines_dropped += 1
            elif _norm_key(text) in running:
                drop.add(i)
                stats.running_header_lines_dropped += 1
                if page is not None and page not in chapter_by_page and _is_all_caps(text):
                    chapter_by_page[page] = re.sub(r"\s+", " ", text).strip().rstrip(".")

    kept: list[Line] = []
    for idx, line in enumerate(lines):
        if idx in drop:
            continue
        text = line.text
        if vocab:
            text, n = repair_split_syllables(text, vocab)
            stats.syllables_repaired += n
        kept.append(Line(text, line.page))
    return kept, _smooth_chapters(chapter_by_page), stats


def _smooth_chapters(chapter_by_page: dict[int, str]) -> dict[int, str]:
    """Running headers often alternate between two phrasings of the same
    chapter on odd/even pages ("HƯỚNG DẪN GIẢM TẢI CỦA IWGDF" / "HƯỚNG DẪN CỦA
    IWGDF VỀ GIẢM TẢI"); keep the first phrasing until a clearly different
    chapter starts, so the heading path doesn't flip every page."""
    smoothed: dict[int, str] = {}
    current: str | None = None
    for page in sorted(chapter_by_page):
        label = chapter_by_page[page]
        if current is None or _jaccard(_norm_key(label), _norm_key(current)) < 0.6:
            current = label
        smoothed[page] = current
    return smoothed


# --- step 2/3: segmentation and heading detection ---------------------------------

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_PHAN_RE = re.compile(r"^(?:PHẦN|Phần|CHƯƠNG|Chương|PHỤ LỤC|Phụ lục)\s+(?:\d+|[IVX]+)\b")
_ROMAN_RE = re.compile(r"^([IVX]{1,6})\.\s+\S")
_VI_UPPER = "A-ZĐÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴ"
# Upper-case first letter required: "2.5 mg ..." / "1.5 mmol/L" are body text.
_DECIMAL_RE = re.compile(rf"^(\d{{1,2}}(?:\.\d{{1,2}}){{1,4}})\.?\s+[{_VI_UPPER}]")
_NUMBERED_RE = re.compile(rf"^(\d{{1,2}})\.\s+[{_VI_UPPER}]")
NUMBERED_HEADING_MAX_CHARS = 90
_BOLD_LINE_RE = re.compile(r"^\*\*(.+?)\*\*\s*:?\s*$")
_BOLD_LEAD_RE = re.compile(r"^\*\*([^*]{3,120}?)\*\*\s*(.*)$")
_LIST_RE = re.compile(r"^(?:[-–•+*]\s+|[a-zđ]\)\s+|\d{1,2}\)\s+|\(\d{1,2}\)\s+)")
_CAPTION_RE = re.compile(r"^(?:Bảng|Hình|Sơ đồ|Biểu đồ)\s+\d+\b")
_TABLE_ROW_RE = re.compile(r"^\s*\|")
_IMAGE_RE = re.compile(r"^!\[[^\]]*\]\([^)]*\)\s*$")
_TERMINAL_RE = re.compile(r"[.!?:;…]\s*$")
_CURATOR_ANNOTATION_RE = re.compile(r"\[(Lưu ý|Chuyển đổi ảnh)", re.IGNORECASE)

HEADING_MAX_CHARS = 120

# Sections that are not useful as retrieval targets: bibliographies (pure
# citations, they match any query that shares an author's or drug's name),
# tables of contents, acknowledgements.
SKIP_SECTION_RE = re.compile(
    r"^(?:TÀI LIỆU THAM KHẢO|Tài liệu tham khảo|MỤC LỤC|Mục lục|LỜI CẢM ƠN|Lời cảm ơn|REFERENCES)\b"
)


def _is_all_caps(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 4 and sum(c.isupper() for c in letters) / len(letters) >= 0.9


def _strip_bold(text: str) -> str:
    """Drop markdown bold markers and any stray italic `*` at either end
    ("***8. Các loại thuốc viên phối hợp***")."""
    return text.replace("**", "").strip().strip("*").strip()


@dataclass
class Segment:
    kind: str  # "heading" | "prose" | "list" | "table" | "note"
    text: str
    page_start: int | None
    page_end: int | None
    rank: int = 0  # headings only: 1 = top level, larger = deeper

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


def _heading_rank(text: str, *, pdf: bool) -> int | None:
    """Rank of a heading line (1 = top), or None if it isn't one. Numbered
    single-level candidates ("3. Điều trị nhiễm trùng") are only accepted
    here provisionally — segment_lines applies the PDF sequence check.

    Unnumbered ALL-CAPS lines rank with "N." headings rather than above them:
    they are real section titles in IWGDF ("CHẨN ĐOÁN", "KHUYẾN CÁO"), but in
    QĐ 1530 they are also flowchart boxes, which must not become the parent
    of every numbered section that follows."""
    label = _strip_bold(text).rstrip(":").strip()
    if not label or len(label) > HEADING_MAX_CHARS:
        return None
    if _PHAN_RE.match(label) or _ROMAN_RE.match(label):
        return 1
    if m := _DECIMAL_RE.match(label):
        return 1 + m.group(1).count(".") + 1
    if (
        _NUMBERED_RE.match(label)
        and len(label) <= NUMBERED_HEADING_MAX_CHARS
        and not label.rstrip().endswith((",", ";"))
    ):
        return 2
    if (
        pdf
        and _is_all_caps(label)
        and len(label) <= 80
        and len(label.split()) >= 2
        and not label.endswith((".", ",", ";"))
    ):
        return 2
    return None


def _is_strong_heading(text: str) -> bool:
    """PHẦN / Roman / 1.1. / ALL-CAPS "3. ĐÁNH GIÁ ..." lines: unambiguous
    enough to end a PDF paragraph even when the previous line has no closing
    punctuation (a figure caption, say)."""
    label = _strip_bold(text)
    if len(label) > HEADING_MAX_CHARS:
        return False
    if _PHAN_RE.match(label) or _ROMAN_RE.match(label) or _DECIMAL_RE.match(label):
        return True
    return bool(_NUMBERED_RE.match(label)) and _is_all_caps(label)


def _clean_heading(text: str) -> str:
    label = _strip_bold(text)
    label = _TOC_LEADER_RE.sub("", label)
    return re.sub(r"\s+", " ", label).strip().rstrip(":").strip()


def segment_lines(lines: list[Line], *, pdf: bool) -> list[Segment]:
    """Turn lines into typed segments. PDF text is hard-wrapped at layout
    width, so prose lines are re-joined until a sentence ends and the next
    line starts a new one (or a heading / list item / caption starts)."""
    segments: list[Segment] = []
    numbered_expect: int | None = None  # last accepted single-level number
    i, n = 0, len(lines)

    def emit(kind: str, text: str, p0: int | None, p1: int | None, rank: int = 0) -> None:
        text = text.strip()
        if text:
            segments.append(Segment(kind, text, p0, p1, rank))

    def numbered_candidates_dense(at: int) -> bool:
        """3+ numbered lines within a few lines of each other are a list (or a
        reference list), not a run of section headings."""
        window = [ln.text.strip() for ln in lines[at : at + 5] if ln.text.strip()]
        return sum(1 for t in window[:4] if _NUMBERED_RE.match(_strip_bold(t))) >= 3

    while i < n:
        raw = lines[i].text
        stripped = raw.strip()
        page = lines[i].page
        if not stripped or _IMAGE_RE.match(stripped):
            i += 1
            continue

        # Curator blockquote (image-to-text conversions, "[Lưu ý ...]" notes).
        if stripped.startswith(">"):
            buf, p0 = [], page
            while i < n and lines[i].text.strip().startswith(">"):
                buf.append(re.sub(r"^>\s?", "", lines[i].text.strip()))
                p1 = lines[i].page
                i += 1
            emit("note", "\n".join(b for b in buf if b.strip()), p0, p1)
            continue

        # A bold heading wrapped across lines in HTML-converted files
        # ("**4. Tiêu chuẩn để làm xét nghiệm chẩn" / "đoán ĐTĐ**").
        if not pdf and stripped.startswith("**") and stripped.count("**") % 2 == 1:
            j, joined = i + 1, stripped
            while j < n and j <= i + 3 and lines[j].text.strip() and joined.count("**") % 2 == 1:
                joined = f"{joined} {lines[j].text.strip()}"
                j += 1
            if joined.count("**") % 2 == 0 and len(_strip_bold(joined)) <= HEADING_MAX_CHARS + 40:
                for k in range(i + 1, j):
                    lines[k] = Line("", lines[k].page)
                stripped = joined

        if m := _MD_HEADING_RE.match(stripped):
            level = len(m.group(1))
            if level > 1:  # H1 is the document title, carried separately
                emit("heading", _clean_heading(m.group(2)), page, page, rank=level - 1)
            i += 1
            continue

        if _TABLE_ROW_RE.match(stripped):
            buf, p0 = [], page
            while i < n and _TABLE_ROW_RE.match(lines[i].text):
                row = lines[i].text.strip()
                if not re.fullmatch(r"\|[\s:|-]*\|?", row):  # drop |---|---| rulers
                    buf.append(row)
                p1 = lines[i].page
                i += 1
            emit("table", "\n".join(buf), p0, p1)
            continue

        rank = _heading_rank(stripped, pdf=pdf) if (pdf or _BOLD_LINE_RE.match(stripped)) else None
        if rank is None and not pdf and _BOLD_LINE_RE.match(stripped):
            label = _strip_bold(stripped)
            if len(label) <= HEADING_MAX_CHARS and not _TERMINAL_RE.search(label.rstrip(":")):
                rank = 6  # unnumbered bold line: lowest-level sub-heading
        # PDF only: a single-level "N. Xxx" line is a heading if it continues
        # the numbering (1, or previous + 1) and is not part of a dense list.
        # This is what rejects IWGDF's numbered recommendations ("27. Để điều
        # trị ...") that v1 carried as a section heading across 77 chunks.
        # In web/HTML files the line was already bold, which is evidence enough.
        # An ALL-CAPS numbered line ("3. ĐÁNH GIÁ LOÉT BÀN CHÂN ĐTĐ") is a
        # section title even when the sequence check lost track.
        numbered = _NUMBERED_RE.match(_strip_bold(stripped))
        if pdf and rank == 2 and numbered and not _DECIMAL_RE.match(_strip_bold(stripped)):
            number = int(numbered.group(1))
            in_sequence = number == 1 or (numbered_expect is not None and number == numbered_expect + 1)
            if _is_all_caps(_strip_bold(stripped)) or (in_sequence and not numbered_candidates_dense(i)):
                numbered_expect = number
            else:
                rank = None
        if rank is not None:
            if rank == 1:
                numbered_expect = None
            text, p0 = stripped, page
            i += 1
            # A heading wrapped onto a second line that continues lower-case.
            if (
                pdf
                and i < n
                and lines[i].text.strip()[:1].islower()
                and not _LIST_RE.match(lines[i].text.strip())
                and not _TERMINAL_RE.search(text)
                and len(text) < HEADING_MAX_CHARS
            ):
                text = f"{text} {lines[i].text.strip()}"
                i += 1
            # "PHẦN 6" alone, its title on the next line ("HẠ ĐƯỜNG HUYẾT").
            elif pdf and _PHAN_RE.fullmatch(_clean_heading(text)):
                j = i
                while j < n and not lines[j].text.strip():
                    j += 1
                if j < n and _is_all_caps(lines[j].text) and len(lines[j].text.strip()) <= 80:
                    text = f"{_clean_heading(text)}: {lines[j].text.strip()}"
                    i = j + 1
            emit("heading", _clean_heading(text), p0, p0, rank=rank)
            continue

        # Bold lead-in on a prose line ("**Chỉ số GI là gì?** GI là ...").
        if not pdf and (m := _BOLD_LEAD_RE.match(stripped)) and m.group(2):
            lead = m.group(1).strip()
            if lead.endswith(("?", ":")) or len(lead) <= 60:
                emit("heading", _clean_heading(lead), page, page, rank=6)
                lines[i] = Line(m.group(2), page)
                continue

        if _CAPTION_RE.match(_strip_bold(stripped)) and pdf:
            # PDF tables come out as one cell per line under a "Bảng N:"
            # caption; keep the rows line-broken and together until the next
            # heading, caption or the table budget.
            buf, p0, p1 = [stripped], page, page
            i += 1
            while i < n:
                nxt = lines[i].text.strip()
                if nxt and (_CAPTION_RE.match(nxt) or nxt.startswith(">") or _heading_rank(nxt, pdf=True) in (1, 2, 3)):
                    break
                if nxt:
                    buf.append(nxt)
                    p1 = lines[i].page
                i += 1
                if estimate_tokens("\n".join(buf)) >= TABLE_MAX_TOKENS:
                    break
            emit("table", "\n".join(buf), p0, p1)
            continue

        # Prose paragraph or list item.
        kind = "list" if _LIST_RE.match(stripped) else "prose"
        buf, p0, p1 = [stripped], page, page
        i += 1
        while i < n:
            nxt_raw = lines[i].text
            nxt = nxt_raw.strip()
            if not nxt:
                # Blank line: a hard break in web files; in PDF text only if
                # the sentence is finished (blank lines also appear mid-sentence
                # at column and page breaks).
                if not pdf or _TERMINAL_RE.search(buf[-1]):
                    break
                i += 1
                continue
            if (
                nxt.startswith((">", "#", "|"))
                or _IMAGE_RE.match(nxt)
                or _LIST_RE.match(nxt)
                or _CAPTION_RE.match(_strip_bold(nxt))
                or (pdf and _is_strong_heading(nxt))
                or (pdf and _heading_rank(nxt, pdf=True) is not None and _TERMINAL_RE.search(buf[-1]))
                or (not pdf and _BOLD_LINE_RE.match(nxt))
            ):
                break
            if pdf and _TERMINAL_RE.search(buf[-1]) and (nxt[:1].isupper() or nxt[:1].isdigit()):
                break
            buf.append(nxt)
            p1 = lines[i].page
            i += 1
        emit(kind, re.sub(r"[ \t]+", " ", " ".join(buf)), p0, p1)

    return segments


# --- step 3: sections and packing ------------------------------------------------


@dataclass
class Section:
    path: list[str]
    segments: list[Segment] = field(default_factory=list)


_HEADING_NUMBER_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2})*)\.?\s")


def _heading_number(label: str) -> str | None:
    """ "5.2.3. Dinh dưỡng" -> "5.2.3", "3. Phân tầng" -> "3", "PHẦN 4" -> None."""
    m = _HEADING_NUMBER_RE.match(label)
    return m.group(1) if m else None


def build_sections(segments: list[Segment], chapter_by_page: dict[int, str]) -> tuple[list[Section], int]:
    """Group segments under their heading path. A chapter label (running
    header) change resets the heading stack. Returns the sections and how
    many segments were dropped as bibliography/TOC/acknowledgements."""
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    chapter: str | None = None
    skip_rank: int | None = None
    skipped = 0
    current = Section(path=[])

    def path() -> list[str]:
        return ([chapter] if chapter else []) + [label for _, label in stack]

    for seg in segments:
        page_chapter = chapter_by_page.get(seg.page_start) if seg.page_start is not None else None
        if page_chapter and page_chapter != chapter:
            chapter = page_chapter
            stack = []
            skip_rank = None
            if current.segments:
                sections.append(current)
            current = Section(path=path())

        if seg.kind == "heading":
            if skip_rank is not None and seg.rank > skip_rank:
                skipped += 1
                continue
            skip_rank = None
            while stack and stack[-1][0] >= seg.rank:
                stack.pop()
            # "5.2.3." under "3. ..." means the "4."/"5." headings in between
            # were missed (PDF line breaks) — drop numbered ancestors whose
            # number isn't a prefix of this one instead of nesting under them.
            if (number := _heading_number(seg.text)) and "." in number:
                while stack and (parent := _heading_number(stack[-1][1])) and not number.startswith(f"{parent}."):
                    stack.pop()
            stack.append((seg.rank, seg.text))
            if current.segments:
                sections.append(current)
            current = Section(path=path())
            if SKIP_SECTION_RE.match(seg.text):
                skip_rank = seg.rank
            continue

        if skip_rank is not None:
            skipped += 1
            continue
        current.segments.append(seg)

    if current.segments:
        sections.append(current)
    return sections, skipped


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;…])\s+(?=[\"“(\[]?[A-ZĐÀ-Ỹ0-9])")


def _split_oversized(seg: Segment) -> list[Segment]:
    """Split one segment that alone exceeds the budget: prose on sentence
    boundaries, tables/notes/lists on lines (a table's caption or header row
    is repeated on every piece so each piece still says what it is)."""
    limit = TABLE_MAX_TOKENS if seg.kind == "table" else MAX_TOKENS
    if seg.tokens <= limit:
        return [seg]
    if seg.kind == "prose":
        units = [u for u in _SENTENCE_SPLIT_RE.split(seg.text) if u.strip()]
        joiner, header = " ", ""
    else:
        units = [u for u in seg.text.split("\n") if u.strip()]
        joiner = "\n"
        header = units.pop(0) if seg.kind == "table" and len(units) > 1 else ""
    # A single unit still over budget (a run-on "sentence") is cut by words.
    fine: list[str] = []
    for unit in units:
        if estimate_tokens(unit) <= MAX_TOKENS:
            fine.append(unit)
            continue
        words, piece = unit.split(" "), []
        for word in words:
            piece.append(word)
            if estimate_tokens(" ".join(piece)) >= TARGET_TOKENS:
                fine.append(" ".join(piece))
                piece = []
        if piece:
            fine.append(" ".join(piece))

    pieces: list[Segment] = []
    buf: list[str] = []
    for unit in fine:
        candidate = joiner.join(([header] if header else []) + buf + [unit])
        if buf and estimate_tokens(candidate) > TARGET_TOKENS:
            pieces.append(
                Segment(seg.kind, joiner.join(([header] if header else []) + buf), seg.page_start, seg.page_end)
            )
            # Sentence overlap for prose only: carry the last sentence over
            # when it is short enough, so a fact straddling the cut survives.
            buf = [buf[-1]] if seg.kind == "prose" and estimate_tokens(buf[-1]) <= OVERLAP_TOKENS else []
        buf.append(unit)
    if buf:
        pieces.append(Segment(seg.kind, joiner.join(([header] if header else []) + buf), seg.page_start, seg.page_end))
    return pieces


@dataclass
class DraftChunk:
    path: list[str]
    segments: list[Segment]

    @property
    def text(self) -> str:
        return "\n".join(s.text for s in self.segments)

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


def pack_section(section: Section) -> list[DraftChunk]:
    pieces = [p for seg in section.segments for p in _split_oversized(seg)]
    chunks: list[DraftChunk] = []
    buf: list[Segment] = []
    for piece in pieces:
        if buf and estimate_tokens("\n".join(s.text for s in [*buf, piece])) > TARGET_TOKENS:
            chunks.append(DraftChunk(section.path, buf))
            buf = []
        buf.append(piece)
    if buf:
        chunks.append(DraftChunk(section.path, buf))
    return chunks


def _common_prefix(a: list[str], b: list[str]) -> list[str]:
    out = []
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        out.append(x)
    return out


def merge_small_chunks(chunks: list[DraftChunk]) -> list[DraftChunk]:
    """Fold a chunk under MIN_TOKENS into its neighbour when both sit under
    the same top-level heading and the result stays within MAX_TOKENS —
    e.g. a drug monograph's one-line "## Dạng bào chế" with the next
    section. The absorbed section's own heading is kept as a line in the text."""
    merged: list[DraftChunk] = []
    for chunk in chunks:
        if merged:
            prev = merged[-1]
            small = prev.tokens < MIN_TOKENS or chunk.tokens < MIN_TOKENS
            same_top = prev.path[:1] == chunk.path[:1]
            if small and same_top and prev.tokens + chunk.tokens <= MAX_TOKENS:
                common = _common_prefix(prev.path, chunk.path)
                extra: list[Segment] = []
                if chunk.path != prev.path and len(chunk.path) > len(common):
                    first = chunk.segments[0]
                    extra = [
                        Segment(
                            "heading",
                            CONTEXT_SEPARATOR.join(chunk.path[len(common) :]),
                            first.page_start,
                            first.page_start,
                        )
                    ]
                if prev.path != common and len(prev.path) > len(common):
                    first = prev.segments[0]
                    head = Segment(
                        "heading", CONTEXT_SEPARATOR.join(prev.path[len(common) :]), first.page_start, first.page_start
                    )
                    prev = DraftChunk(prev.path, [head, *prev.segments])
                merged[-1] = DraftChunk(common, [*prev.segments, *extra, *chunk.segments])
                continue
        merged.append(chunk)
    return merged


# --- public: tier 2 (raw/*.md) -----------------------------------------------------


def context_line(doc_title: str | None, path: list[str], status: str) -> str:
    parts = [doc_title or ""]
    if status == "superseded":
        parts[0] = f"{parts[0]} [VĂN BẢN ĐÃ HẾT HIỆU LỰC]".strip()
    if path:
        parts.append(CONTEXT_SEPARATOR.join(path))
    return "\n".join(p for p in parts if p)


def parse_lines(body: str) -> tuple[list[Line], bool]:
    """Split into lines tagged with their `--- Trang N ---` page (None when
    the file has no markers). Also undoes the "****" artifact where HTML
    bold runs were split mid-word ("đườ****n****g")."""
    body = body.replace("****", "")
    lines: list[Line] = []
    page: int | None = None
    has_markers = False
    for raw in body.splitlines():
        if m := _PAGE_MARKER_RE.match(raw.strip()):
            page = int(m.group(1))
            has_markers = True
            continue
        lines.append(Line(raw.rstrip(), page))
    return lines, has_markers


def chunk_document(
    filename: str,
    content: str,
    *,
    source_url: str | None = None,
    source_info: dict[str, str | None] | None = None,
    syllable_vocab: SyllableVocab | None = None,
    ingested_at: str | None = None,
    stats: dict[str, Any] | None = None,
) -> list[dict]:
    """Chunk one raw/*.md file. `source_info` carries sources.csv fields for
    this file (organization, trust_tier, category); `syllable_vocab` enables
    split-syllable repair on PDF-extracted files. If `stats` is given it is
    filled with per-file cleaning/chunking counts for the manifest."""
    ingested_at = ingested_at or _now_iso()
    source_info = source_info or {}
    header_url, crawl_date, header_end = parse_file_header(content)
    doc_title = extract_doc_title(content)
    status = detect_status(content)
    source_content_hash = compute_hash(content)

    lines, is_pdf = parse_lines(content[header_end:])
    clean = CleanStats()
    chapter_by_page: dict[int, str] = {}
    if is_pdf:
        lines, chapter_by_page, clean = clean_pdf_lines(lines, syllable_vocab)

    segments = segment_lines(lines, pdf=is_pdf)
    sections, skipped = build_sections(segments, chapter_by_page)
    drafts = merge_small_chunks([c for s in sections for c in pack_section(s)])

    base = {
        "tier": 2,
        "source_file": filename,
        "doc_title": doc_title,
        "doc_type": doc_type_for(filename),
        "status": status,
        "organization": source_info.get("organization"),
        "trust_tier": source_info.get("trust_tier"),
        "category": source_info.get("category"),
        "source_url": source_url or header_url,
        "crawl_date": crawl_date,
        "is_pdf_extract": is_pdf,
        "source_content_hash": source_content_hash,
        "chunker_version": CHUNKER_VERSION,
        "corpus_version": CORPUS_VERSION,
        "ingested_at": ingested_at,
    }
    chunks: list[dict] = []
    for index, draft in enumerate(drafts):
        body = draft.text
        text = f"{context_line(doc_title, draft.path, status)}\n\n{body}"
        pages = [p for s in draft.segments for p in (s.page_start, s.page_end) if p is not None]
        content_hash = compute_hash(text)
        chunks.append(
            {
                "chunk_id": make_chunk_id("t2", filename, str(index), content_hash),
                **base,
                "heading_path": CONTEXT_SEPARATOR.join(draft.path) or None,
                "section_heading": draft.path[-1] if draft.path else None,
                "page_start": min(pages) if pages else None,
                "page_end": max(pages) if pages else None,
                "chunk_index": index,
                "n_tokens": estimate_tokens(text),
                "has_curator_annotation": bool(_CURATOR_ANNOTATION_RE.search(body)),
                "content_hash": content_hash,
                "text": text,
            }
        )

    if stats is not None:
        stats.update(
            {
                "chunks": len(chunks),
                "is_pdf_extract": is_pdf,
                "status": status,
                "headings": sum(1 for s in segments if s.kind == "heading"),
                "segments_skipped_as_references_toc": skipped,
                "toc_pages_dropped": clean.toc_pages_dropped,
                "page_number_lines_dropped": clean.page_number_lines_dropped,
                "running_header_lines_dropped": clean.running_header_lines_dropped,
                "syllables_repaired": clean.syllables_repaired,
            }
        )
    return chunks


# --- Tier 1 (sources.csv) ------------------------------------------------------

SKIP_NO_LOCAL_FILE = "no_local_file"
SKIP_FAILED_CRAWL = "failed_crawl"

_FAILED_CRAWL_NOTE_RE = re.compile(r"thất bại|failed|blocked|chặn|403", re.IGNORECASE)


def classify_tier1_skip(row: dict[str, str]) -> str | None:
    """Returns a skip reason (SKIP_NO_LOCAL_FILE / SKIP_FAILED_CRAWL) or None
    if the row should be embedded. Rows with no local_file are citation-only
    (kept out of the index); rows whose notes flag a failed crawl are a
    distinguishable sub-reason, logged separately in the manifest."""
    if (row.get("local_file") or "").strip():
        return None
    if _FAILED_CRAWL_NOTE_RE.search(row.get("notes") or ""):
        return SKIP_FAILED_CRAWL
    return SKIP_NO_LOCAL_FILE


def build_tier1_chunk(
    row: dict[str, str], row_id: int, *, status: str = "active", ingested_at: str | None = None
) -> dict:
    """One sources.csv row = one chunk. `chunk_id` stays derived from (row_id,
    key_content) only — the retrieval eval's gold labels key on it — while the
    embedded text gains the document title as its context line, since
    key_content alone often never names the document or table it is from."""
    ingested_at = ingested_at or _now_iso()
    key_content = (row.get("key_content") or "").strip()
    document_title = (row.get("document_title") or "").strip() or None
    text = f"{context_line(document_title, [], status)}\n\n{key_content}" if document_title else key_content
    return {
        "chunk_id": make_chunk_id("t1", "sources_csv", str(row_id), key_content),
        "tier": 1,
        "row_id": row_id,
        "trust_tier": (row.get("tier") or "").strip() or None,
        "category": (row.get("category") or "").strip() or None,
        "organization": (row.get("organization") or "").strip() or None,
        "document_title": document_title,
        "source_url": (row.get("url") or "").strip() or None,
        "local_file": (row.get("local_file") or "").strip() or None,
        "page_ref": (row.get("page_ref") or "").strip() or None,
        "curator_notes": (row.get("notes") or "").strip() or None,
        "status": status,
        "n_tokens": estimate_tokens(text),
        "content_hash": compute_hash(text, CHUNKER_VERSION),
        "chunker_version": CHUNKER_VERSION,
        "corpus_version": CORPUS_VERSION,
        "ingested_at": ingested_at,
        "text": text,
    }
