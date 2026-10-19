"""Track A / Track B chunking logic for the knowledge-base corpus.

OWNER: RAG data-pipeline workstream. Do not edit from other workstreams.

See docs/AI_Summary_Respond_System_Design.md §5.2 (chunking algorithm) and
§5.3 (metadata schema each chunk must carry). Deliberately kept free of any
embeddings-API / chromadb import so it can be unit tested in isolation
(codebase/models/rag/ingest.py owns all I/O and API calls; this module is
pure string-in, dict-out).

Track classification (§5.4: "a one-time manual classification, not an
auto-detected runtime branch") was verified directly against the corpus
with `grep -c "^## "` per file before writing this list, not assumed from
the design doc's own file-shape description — which turned out to disagree
with the corpus on one detail: the design doc's §5.2 draft describes
`byt-5481-qd-byt-full-text.md` as having *no* "--- Trang N ---" page
markers and `byt-3319-qd-byt-full-text.md` as having them. Direct
inspection of the current files shows the opposite (byt-5481: 77 page
markers; byt-3319: 0). The five files below are still correctly the
long-form/near-flat Track B set (confirmed heading-density outliers), but
`chunk_track_b` below detects page markers from the file itself rather than
hard-coding which of the five has them, so it is correct either way.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

EMBEDDING_MODEL = "text-embedding-3-small"
CORPUS_VERSION = "1.0.0"

# --- Track assignment -------------------------------------------------------
# Explicit, per §5.4: "ingest.py ships with an explicit file-list-to-track
# mapping (a small dict/config, not a guess), and adding a 45th file means
# adding one line to that mapping, not trusting a regex to classify it
# correctly." classify_file() below raises rather than silently defaulting
# an unmapped file to Track A, so a new file forces that one-line addition.

TRACK_B_FILES: frozenset[str] = frozenset(
    {
        "byt-5481-qd-byt-full-text.md",
        "byt-3280-qd-byt-full-text.md",
        "byt-3319-qd-byt-full-text.md",
        "byt-1530-qd-byt-loet-ban-chan-full-text.md",
        "iwgdf-2019-guideline-full-text.md",
    }
)

TRACK_A_FILES: frozenset[str] = frozenset(
    {
        "ada-2026-glycemic-goals-hypoglycemia.md",
        "ada-2026-older-adults-glycemic-targets.md",
        "ada-standards-2026-nutrition-changes.md",
        "ada-standards-2026-press-release.md",
        "ada-weekly-exercise-targets.md",
        "bachmai-insulin-ky-thuat-tiem.md",
        "bachmai-trung-tam-dinh-duong-lam-sang.md",
        "benhviennoitiet-cham-soc-ban-chan-van-dong.md",
        "benhviennoitiet-che-do-luyen-tap-dai-thao-duong.md",
        "duocthu-acarbose-monograph.md",
        "duocthu-dapagliflozin-monograph.md",
        "duocthu-empagliflozin-monograph.md",
        "duocthu-gliclazide-monograph.md",
        "duocthu-glimepiride-monograph.md",
        "duocthu-insulin-monograph.md",
        "duocthu-liraglutide-monograph.md",
        "duocthu-metformin-monograph.md",
        "duocthu-pioglitazone-monograph.md",
        "duocthu-repaglinide-monograph.md",
        "duocthu-semaglutide-monograph.md",
        "duocthu-sitagliptin-monograph.md",
        "duocthu-vildagliptin-monograph.md",
        "medinet-che-do-dinh-duong-can-bang-dai-thao-duong.md",
        "vade-10-diem-hieu-dung-ve-benh-dtd.md",
        "vade-an-kieng-va-ton-giao.md",
        "vade-cham-soc-ban-chan-phong-loet.md",
        "vade-chi-so-gi.md",
        "vade-kham-mat-dinh-ky.md",
        "vade-nhan-biet-xu-tri-tang-duong-huyet-nguy-hiem.md",
        "vade-sick-day-rules.md",
        "vade-tam-ly-stress-tram-cam.md",
        "vade-thoi-diem-tap-the-duc-an-toan.md",
        "vade-thuoc-gay-tang-ha-glucose.md",
        "vade-tong-quan-benh-dtd.md",
        "viendinhduong-tra-cuu-thuc-pham.md",
        "vinmec-cac-nhom-thuoc-dieu-tri-tieu-duong-type-2.md",
        "vinmec-diamicron-gliclazide-luu-y.md",
        "vinmec-metformin-cach-dung-luu-y.md",
        "vncdc-dinh-duong-hop-ly-dai-thao-duong.md",
        "who-2020-physical-activity-guidelines.md",
    }
)

# NOTE: the design doc's §5 prose says "39 short files" / "44 files" total.
# Direct inspection of data/knowledge_base/raw/*.md (os.listdir, not eyeballing
# a paginated `ls`) shows 45 files on disk, matching sources.csv's local_file
# references exactly (44 vs 45 was a miscount somewhere upstream of this
# module, not a file that was later added/removed — see final report). The 5
# Track B files match the design doc precisely; the remaining 40 are Track A.
assert len(TRACK_A_FILES) == 40, f"expected 40 Track A files, got {len(TRACK_A_FILES)}"
assert len(TRACK_B_FILES) == 5, f"expected 5 Track B files, got {len(TRACK_B_FILES)}"
assert not (TRACK_A_FILES & TRACK_B_FILES)


def classify_file(filename: str) -> str:
    """Return "A" or "B". Raises for any file not in the explicit mapping
    above — see module docstring: this is intentional, not a bug."""
    if filename in TRACK_B_FILES:
        return "B"
    if filename in TRACK_A_FILES:
        return "A"
    raise ValueError(
        f"{filename!r} is not in TRACK_A_FILES or TRACK_B_FILES — add it to one of those "
        "sets in codebase/models/rag/chunking.py (§5.4: track assignment is a manual, "
        "one-time classification, not an auto-detected runtime branch)."
    )


# --- token estimation --------------------------------------------------------
# tiktoken's cl100k_base encoding is what text-embedding-3-small uses. Loaded
# lazily and defensively: a fresh sandbox with no network/no cache must not
# make chunking (or the tests that exercise it) hang or fail — fall back to a
# chars/4 heuristic if the encoder can't be loaded.

_ENCODING: Any = None
_ENCODING_LOAD_ATTEMPTED = False


def _get_encoding() -> Any:
    global _ENCODING, _ENCODING_LOAD_ATTEMPTED
    if not _ENCODING_LOAD_ATTEMPTED:
        _ENCODING_LOAD_ATTEMPTED = True
        try:
            import tiktoken

            _ENCODING = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _ENCODING = None
    return _ENCODING


def estimate_tokens(text: str) -> int:
    encoding = _get_encoding()
    if encoding is not None:
        try:
            return len(encoding.encode(text))
        except Exception:
            pass
    return max(1, len(text) // 4)


# --- shared helpers -----------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def compute_hash(*parts: str) -> str:
    """Full sha256 hex digest over `parts`, joined with a separator that
    won't appear in real content."""
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8"))
    return digest.hexdigest()


def make_chunk_id(prefix: str, *parts: str) -> str:
    return f"{prefix}-{compute_hash(*parts)[:16]}"


_HEADER_LINE_RE = re.compile(r"^>\s?(.*)$")
_SOURCE_URL_RE = re.compile(r"Nguồn(?: tệp gốc)?:\s*(\S.*)$")
_CRAWL_DATE_RE = re.compile(r"(?:Crawl ngày|Trích xuất ngày):\s*(\S+)")


def parse_file_header(content: str) -> tuple[str | None, str | None, int]:
    """Parse the leading `> Nguồn: ...` / `> Crawl ngày: ...` blockquote
    block most raw/*.md files open with. Returns (source_url, crawl_date,
    body_start_offset). All three are best-effort: some files (byt-5481)
    have no header at all, in which case this returns (None, None, 0).
    """
    lines = content.splitlines(keepends=True)
    source_url: str | None = None
    crawl_date: str | None = None
    offset = 0
    for line in lines:
        stripped = line.rstrip("\n")
        if not stripped.strip():
            offset += len(line)
            continue
        m = _HEADER_LINE_RE.match(stripped)
        if not m:
            break
        body = m.group(1)
        url_match = _SOURCE_URL_RE.search(body)
        if url_match:
            source_url = url_match.group(1).strip()
        date_match = _CRAWL_DATE_RE.search(body)
        if date_match:
            crawl_date = date_match.group(1).strip()
        offset += len(line)
    return source_url, crawl_date, offset


_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_QUOTED_TITLE_RE = re.compile(r'tài liệu chuyên môn\s*["“]([^"”]+)["”]')


def extract_doc_title(content: str) -> str | None:
    """First H1 line, verbatim. Falls back to the curator's own quoted
    "tài liệu chuyên môn "X"" phrasing for the one file with no H1 at all
    (byt-5481) — this is real text pulled from the document body, not a
    fabricated title."""
    h1 = _H1_RE.search(content)
    if h1:
        return h1.group(1).strip()
    quoted = _QUOTED_TITLE_RE.search(content)
    if quoted:
        return quoted.group(1).strip()
    return None


_BLANK_LINE_RE = re.compile(r"\n\s*\n+")


def split_paragraphs_with_offsets(text: str, base_offset: int = 0) -> list[tuple[int, int, str]]:
    """Split on blank-line boundaries. Returns (start, end, text) tuples
    with offsets relative to the original file when `base_offset` is the
    offset of `text` within that file — used for the char_start/char_end
    debugging-traceback metadata field (§5.3)."""
    paragraphs: list[tuple[int, int, str]] = []
    pos = 0
    for m in _BLANK_LINE_RE.finditer(text):
        raw = text[pos : m.start()]
        stripped = raw.strip()
        if stripped:
            lead = len(raw) - len(raw.lstrip())
            start = base_offset + pos + lead
            paragraphs.append((start, start + len(stripped), stripped))
        pos = m.end()
    raw = text[pos:]
    stripped = raw.strip()
    if stripped:
        lead = len(raw) - len(raw.lstrip())
        start = base_offset + pos + lead
        paragraphs.append((start, start + len(stripped), stripped))
    return paragraphs


def _tail_overlap(paragraphs: list[tuple[int, int, str]], overlap_tokens: int) -> list[tuple[int, int, str]]:
    tail: list[tuple[int, int, str]] = []
    total = 0
    for para in reversed(paragraphs):
        if total >= overlap_tokens and tail:
            break
        tail.append(para)
        total += estimate_tokens(para[2])
    tail.reverse()
    return tail


def group_paragraphs(
    paragraphs: list[tuple[int, int, str]], max_tokens: int, overlap_tokens: int
) -> list[list[tuple[int, int, str]]]:
    """Greedy budget-based grouping with a sliding-window overlap carried
    forward between groups. A single paragraph larger than `max_tokens` is
    still emitted whole (never split mid-paragraph) — this is what keeps an
    already-atomic table block intact even when oversized (§5.2)."""
    groups: list[list[tuple[int, int, str]]] = []
    current: list[tuple[int, int, str]] = []
    current_tokens = 0
    for para in paragraphs:
        p_tokens = estimate_tokens(para[2])
        if current and current_tokens + p_tokens > max_tokens:
            groups.append(current)
            current = _tail_overlap(current, overlap_tokens)
            current_tokens = sum(estimate_tokens(p[2]) for p in current)
        current.append(para)
        current_tokens += p_tokens
    if current:
        groups.append(current)
    return groups


def _emit_windowed_chunks(
    full_text: str, paragraphs: list[tuple[int, int, str]], max_tokens: int, overlap_tokens: int
) -> list[tuple[int, int, str]]:
    """Groups paragraphs by token budget, then slices the *original* source
    text at each group's (start, end) offsets rather than re-joining
    paragraph strings — guarantees char_start/char_end (§5.3's debugging
    traceback field) exactly match the returned text, including whatever
    exact whitespace/blank-line pattern the source actually had."""
    groups = group_paragraphs(paragraphs, max_tokens, overlap_tokens)
    out = []
    for group in groups:
        start = group[0][0]
        end = group[-1][1]
        out.append((start, end, full_text[start:end]))
    return out


# --- Track A ------------------------------------------------------------------

_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)

TRACK_A_MAX_TOKENS = 800
TRACK_A_OVERLAP_TOKENS = 100


def chunk_track_a(
    filename: str,
    content: str,
    *,
    source_url: str | None = None,
    ingested_at: str | None = None,
) -> list[dict]:
    """§5.2 Track A: split at `##` boundaries; a section that's still too
    long falls back to an ~800-token window with ~100-token overlap.

    `source_url` lets the caller (ingest.py) pass the canonical URL looked
    up from sources.csv, overriding the file's own header parse — falls
    back to the in-file header if not given.
    """
    ingested_at = ingested_at or _now_iso()
    header_url, crawl_date, header_end = parse_file_header(content)
    resolved_source_url = source_url or header_url
    doc_title = extract_doc_title(content)
    source_content_hash = compute_hash(content)

    h1 = _H1_RE.search(content)
    body_start = h1.end() if h1 else header_end

    h2_matches = list(_H2_RE.finditer(content))
    sections: list[tuple[str | None, int, int]] = []
    if not h2_matches:
        sections.append((None, body_start, len(content)))
    else:
        if h2_matches[0].start() > body_start:
            sections.append((None, body_start, h2_matches[0].start()))
        for idx, m in enumerate(h2_matches):
            heading = m.group(1).strip()
            start = m.start()
            end = h2_matches[idx + 1].start() if idx + 1 < len(h2_matches) else len(content)
            sections.append((heading, start, end))

    chunks: list[dict] = []
    chunk_index = 0
    for heading, start, end in sections:
        raw = content[start:end]
        stripped = raw.strip()
        if not stripped:
            continue
        lead = len(raw) - len(raw.lstrip())
        real_start = start + lead
        if estimate_tokens(stripped) <= TRACK_A_MAX_TOKENS:
            pieces = [(real_start, real_start + len(stripped), stripped)]
        else:
            paras = split_paragraphs_with_offsets(stripped, real_start)
            pieces = _emit_windowed_chunks(content, paras, TRACK_A_MAX_TOKENS, TRACK_A_OVERLAP_TOKENS)
        for c_start, c_end, c_text in pieces:
            chunks.append(
                _build_track_a_chunk(
                    filename=filename,
                    doc_title=doc_title,
                    section_heading=heading,
                    chunk_index=chunk_index,
                    char_start=c_start,
                    char_end=c_end,
                    text=c_text,
                    source_url=resolved_source_url,
                    crawl_date=crawl_date,
                    ingested_at=ingested_at,
                    source_content_hash=source_content_hash,
                )
            )
            chunk_index += 1
    return chunks


def _build_track_a_chunk(
    *,
    filename: str,
    doc_title: str | None,
    section_heading: str | None,
    chunk_index: int,
    char_start: int,
    char_end: int,
    text: str,
    source_url: str | None,
    crawl_date: str | None,
    ingested_at: str,
    source_content_hash: str,
) -> dict:
    content_hash = compute_hash(text)
    chunk_id = make_chunk_id("t2a", filename, str(chunk_index), content_hash)
    return {
        "chunk_id": chunk_id,
        "tier": 2,
        "track": "A",
        "source_file": filename,
        "doc_title": doc_title,
        "section_heading": section_heading,
        "page_number": None,
        "source_url": source_url,
        "crawl_date": crawl_date,
        "chunk_index": chunk_index,
        "char_start": char_start,
        "char_end": char_end,
        "has_curator_annotation": bool(_CURATOR_ANNOTATION_RE.search(text)),
        "content_hash": content_hash,
        # Whole-file hash, duplicated onto every chunk from this file — not
        # in the design doc's illustrative §5.3 example, but needed to make
        # §5.9's "re-embed only what changed" cheap: a single collection.get
        # on any one existing chunk for this source_file tells ingest.py
        # whether the file changed at all, without re-chunking or
        # re-embedding to find out. See ingest.py's idempotency step.
        "source_content_hash": source_content_hash,
        "embedding_model": EMBEDDING_MODEL,
        "corpus_version": CORPUS_VERSION,
        "ingested_at": ingested_at,
        "text": text,
    }


# --- Track B ------------------------------------------------------------------

TRACK_B_MAX_TOKENS = 600
TRACK_B_OVERLAP_RATIO = 0.15
# Safety valve for the table-atomicity absorption below: don't let one
# "table run" swallow more than this many tokens even if no closing
# boundary is found (real tables in this corpus are nowhere near this size).
TRACK_B_TABLE_RUN_HARD_CAP = TRACK_B_MAX_TOKENS * 4

_PAGE_MARKER_RE = re.compile(r"^--- Trang (\d+) ---$", re.MULTILINE)
# NOTE: the gap between the marker ("PHẦN N" / "N.") and its heading text is
# deliberately `[ \t]+`, NOT `\s+`. `\s` matches `\n`, and MULTILINE's `^`/`$`
# anchors don't stop it from doing so mid-pattern — only the trailing `.*`
# (no DOTALL) refuses to cross a newline. Confirmed with real corpus content
# (byt-3319-qd-byt-full-text.md, which has zero real pseudo-headers in its
# body): a PDF-extraction line-wrap artifact split "ĐTĐ típ 1." across a line
# break, leaving a bare "1." alone on its own line followed by a blank line
# and then an unrelated paragraph. With `\s+`, `_NUMBERED_HEADER_RE` bridged
# that gap and matched "1.\n\nYếu tố môi trường của ĐTĐ típ 1: virus quai
# bị," as a single fake "header" spanning a paragraph boundary — the only
# pseudo-header this file has, so it got carried forward (§5.2's "last-seen
# match carried forward") across roughly half the file's chunks, mislabeling
# their section_heading with a completely unrelated topic. `[ \t]+` keeps
# the match confined to one physical line, matching the design intent
# ("short numbered top-level lines") and the docstrings below.
_PHAN_HEADER_RE = re.compile(r"^PHẦN[ \t]+\d+.*$", re.MULTILINE)
_NUMBERED_HEADER_RE = re.compile(r"^\d+\.[ \t]+[A-ZĐ].*$", re.MULTILINE)
_NUMBERED_HEADER_MAX_LEN = 80  # "short numbered top-level lines" per §5.2
_TABLE_START_RE = re.compile(r"^Bảng[ \t]+\d+\b")
_TRAILING_DOTS_RE = re.compile(r"[.\s]{4,}\d*\s*$")
_CURATOR_ANNOTATION_RE = re.compile(r"\[(Lưu ý|Chuyển đổi ảnh)", re.IGNORECASE)


def _clean_heading(line: str) -> str:
    """Strip PDF-extracted table-of-contents dot-leaders + trailing page
    number, e.g. "PHẦN 1: ĐỊNH NGHĨA .......... 8" -> "PHẦN 1: ĐỊNH NGHĨA"."""
    return _TRAILING_DOTS_RE.sub("", line).strip()


_DENSE_LIST_MIN_GAP = 400  # chars
_DENSE_LIST_MIN_RUN = 3


def _drop_dense_numbered_runs(
    matches: list[tuple[int, str]], min_gap: int = _DENSE_LIST_MIN_GAP, min_run: int = _DENSE_LIST_MIN_RUN
) -> list[tuple[int, str]]:
    """Numbered reference-list entries ("1. American Diabetes
    Association.", "2. Consensus Statement...") match the same
    `^\\d+\\.\\s+[A-ZĐ]` "short numbered top-level line" shape as a real
    pseudo-header, and this corpus's bibliographies are exactly that shape
    (verified on byt-3319-qd-byt-full-text.md, which has no PHẦN headers at
    all, only a references list at the tail matching this pattern). The
    discriminating signal: a real top-level section header is followed by
    paragraphs of body text before the next one; reference-list entries are
    packed within a few hundred characters of each other. Runs of 3+
    numbered matches closer together than `min_gap` chars are dropped as a
    list, not a header sequence."""
    if not matches:
        return matches
    keep = [True] * len(matches)
    i, n = 0, len(matches)
    while i < n:
        j = i
        while j + 1 < n and (matches[j + 1][0] - matches[j][0]) < min_gap:
            j += 1
        if j - i + 1 >= min_run:
            for k in range(i, j + 1):
                keep[k] = False
        i = j + 1
    return [m for m, k in zip(matches, keep, strict=True) if k]


def find_pseudo_headers(content: str) -> list[tuple[int, str]]:
    """§5.2 pseudo-section detection: `PHẦN \\d+` lines, or short numbered
    top-level lines (`^\\d+\\.\\s+[A-ZĐ]`). Returns (offset, cleaned_text)
    pairs in document order — the last one at-or-before a chunk's start
    offset is that chunk's section_heading (carried forward)."""
    headers: list[tuple[int, str]] = []
    for m in _PHAN_HEADER_RE.finditer(content):
        headers.append((m.start(), _clean_heading(m.group(0))))
    numbered: list[tuple[int, str]] = []
    for m in _NUMBERED_HEADER_RE.finditer(content):
        line = m.group(0)
        if len(line) <= _NUMBERED_HEADER_MAX_LEN:
            numbered.append((m.start(), _clean_heading(line)))
    headers.extend(_drop_dense_numbered_runs(numbered))
    headers.sort(key=lambda h: h[0])
    return headers


def _section_heading_at(headers: list[tuple[int, str]], offset: int) -> str | None:
    heading: str | None = None
    for h_offset, h_text in headers:
        if h_offset <= offset:
            heading = h_text
        else:
            break
    return heading


def find_page_markers(content: str) -> list[tuple[int, int]]:
    """(start_offset_of_marker, page_number) pairs, in document order."""
    return [(m.start(), int(m.group(1))) for m in _PAGE_MARKER_RE.finditer(content)]


def merge_table_runs(
    paragraphs: list[tuple[int, int, str]], hard_cap_tokens: int = TRACK_B_TABLE_RUN_HARD_CAP
) -> list[tuple[int, int, str]]:
    """§5.2: "Never split across a detected Bảng N: (table) marker — keep a
    flagged table block atomic even if it runs over budget." A `Bảng N:`
    paragraph starts a run that absorbs following paragraphs until the next
    `Bảng N:` marker (a new table starting) or a hard token cap (safety
    valve against a pathological run-on absorbing the rest of the file)."""
    merged: list[tuple[int, int, str]] = []
    i, n = 0, len(paragraphs)
    while i < n:
        start, end, text = paragraphs[i]
        if _TABLE_START_RE.match(text.strip()):
            run = [paragraphs[i]]
            run_tokens = estimate_tokens(text)
            j = i + 1
            while j < n and not _TABLE_START_RE.match(paragraphs[j][2].strip()) and run_tokens < hard_cap_tokens:
                run.append(paragraphs[j])
                run_tokens += estimate_tokens(paragraphs[j][2])
                j += 1
            merged_start = run[0][0]
            merged_end = run[-1][1]
            merged_text = "\n\n".join(p[2] for p in run)
            merged.append((merged_start, merged_end, merged_text))
            i = j
        else:
            merged.append(paragraphs[i])
            i += 1
    return merged


def chunk_track_b(
    filename: str,
    content: str,
    *,
    source_url: str | None = None,
    ingested_at: str | None = None,
) -> list[dict]:
    """§5.2 Track B: page markers (when present) are the primary chunk
    boundary and give free page_number metadata; pseudo-headers carry
    forward as section_heading; paragraphs are grouped to a ~600-token
    budget with ~15% overlap, never splitting a detected table block.

    Whether *this* file has page markers is detected from the file itself
    (see module docstring) rather than assumed — works correctly regardless
    of which of the five Track B files does or doesn't have them.
    """
    ingested_at = ingested_at or _now_iso()
    header_url, crawl_date, header_end = parse_file_header(content)
    resolved_source_url = source_url or header_url
    doc_title = extract_doc_title(content)
    source_content_hash = compute_hash(content)

    page_markers = find_page_markers(content)
    pseudo_headers = find_pseudo_headers(content)
    overlap_tokens = max(1, int(TRACK_B_MAX_TOKENS * TRACK_B_OVERLAP_RATIO))

    if page_markers:
        page_blocks: list[tuple[int | None, int, int]] = []
        for idx, (marker_start, page_num) in enumerate(page_markers):
            marker_end = marker_start + len(f"--- Trang {page_num} ---")
            block_start = marker_end
            block_end = page_markers[idx + 1][0] if idx + 1 < len(page_markers) else len(content)
            page_blocks.append((page_num, block_start, block_end))
    else:
        page_blocks = [(None, header_end, len(content))]

    chunks: list[dict] = []
    chunk_index = 0
    for page_num, block_start, block_end in page_blocks:
        block_text = content[block_start:block_end]
        paras = split_paragraphs_with_offsets(block_text, block_start)
        if not paras:
            continue
        merged = merge_table_runs(paras)
        for c_start, c_end, c_text in _emit_windowed_chunks(content, merged, TRACK_B_MAX_TOKENS, overlap_tokens):
            heading = _section_heading_at(pseudo_headers, c_start)
            chunks.append(
                _build_track_b_chunk(
                    filename=filename,
                    doc_title=doc_title,
                    section_heading=heading,
                    page_number=page_num,
                    chunk_index=chunk_index,
                    char_start=c_start,
                    char_end=c_end,
                    text=c_text,
                    source_url=resolved_source_url,
                    crawl_date=crawl_date,
                    ingested_at=ingested_at,
                    source_content_hash=source_content_hash,
                )
            )
            chunk_index += 1
    return chunks


def _build_track_b_chunk(
    *,
    filename: str,
    doc_title: str | None,
    section_heading: str | None,
    page_number: int | None,
    chunk_index: int,
    char_start: int,
    char_end: int,
    text: str,
    source_url: str | None,
    crawl_date: str | None,
    ingested_at: str,
    source_content_hash: str,
) -> dict:
    content_hash = compute_hash(text)
    chunk_id = make_chunk_id("t2b", filename, str(chunk_index), content_hash)
    return {
        "chunk_id": chunk_id,
        "tier": 2,
        "track": "B",
        "source_file": filename,
        "doc_title": doc_title,
        "section_heading": section_heading,
        "page_number": page_number,
        "source_url": source_url,
        "crawl_date": crawl_date,
        "chunk_index": chunk_index,
        "char_start": char_start,
        "char_end": char_end,
        "has_curator_annotation": bool(_CURATOR_ANNOTATION_RE.search(text)),
        "content_hash": content_hash,
        "source_content_hash": source_content_hash,
        "embedding_model": EMBEDDING_MODEL,
        "corpus_version": CORPUS_VERSION,
        "ingested_at": ingested_at,
        "text": text,
    }


def chunk_tier2_file(
    filename: str, content: str, *, source_url: str | None = None, ingested_at: str | None = None
) -> list[dict]:
    """Dispatch to chunk_track_a / chunk_track_b per classify_file()."""
    track = classify_file(filename)
    if track == "A":
        return chunk_track_a(filename, content, source_url=source_url, ingested_at=ingested_at)
    return chunk_track_b(filename, content, source_url=source_url, ingested_at=ingested_at)


# --- Tier 1 (sources.csv) ------------------------------------------------------

SKIP_NO_LOCAL_FILE = "no_local_file"
SKIP_FAILED_CRAWL = "failed_crawl"

_FAILED_CRAWL_NOTE_RE = re.compile(r"thất bại|failed|blocked|chặn|403", re.IGNORECASE)


def classify_tier1_skip(row: dict[str, str]) -> str | None:
    """Returns a skip reason (SKIP_NO_LOCAL_FILE / SKIP_FAILED_CRAWL) or
    None if the row should be embedded. Per §5.1: rows with no local_file
    are skipped (external citation only, kept queryable as citation-only
    metadata, not embedded); rows whose notes explicitly flag a failed
    crawl are a distinguishable sub-reason of the same underlying "no
    content to embed" situation, logged separately in the manifest."""
    local_file = (row.get("local_file") or "").strip()
    if local_file:
        return None
    notes = row.get("notes") or ""
    if _FAILED_CRAWL_NOTE_RE.search(notes):
        return SKIP_FAILED_CRAWL
    return SKIP_NO_LOCAL_FILE


def build_tier1_chunk(row: dict[str, str], row_id: int, *, ingested_at: str | None = None) -> dict:
    """§5.1/§5.3: one sources.csv row = one chunk, embedding key_content
    directly, no splitting. `row_id` is the row's 0-indexed position in the
    CSV body (stable as long as the file isn't reordered) and — together
    with key_content — is what chunk_id is derived from, per §5.3's
    `sha256(source="sources_csv", row_id, key_content)[:16]`.
    """
    ingested_at = ingested_at or _now_iso()
    key_content = (row.get("key_content") or "").strip()
    content_hash = compute_hash(key_content)
    chunk_id = make_chunk_id("t1", "sources_csv", str(row_id), key_content)
    return {
        "chunk_id": chunk_id,
        "tier": 1,
        "row_id": row_id,
        "trust_tier": (row.get("tier") or "").strip() or None,
        "category": (row.get("category") or "").strip() or None,
        "organization": (row.get("organization") or "").strip() or None,
        "document_title": (row.get("document_title") or "").strip() or None,
        "source_url": (row.get("url") or "").strip() or None,
        "local_file": (row.get("local_file") or "").strip() or None,
        "page_ref": (row.get("page_ref") or "").strip() or None,
        "curator_notes": (row.get("notes") or "").strip() or None,
        "content_hash": content_hash,
        "embedding_model": EMBEDDING_MODEL,
        "corpus_version": CORPUS_VERSION,
        "ingested_at": ingested_at,
        "text": key_content,
    }
