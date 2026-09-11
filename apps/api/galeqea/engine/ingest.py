"""Requirement document ingestion.

Extracts text from PDF/DOCX/Markdown/plain text, then splits it into *addressable*
requirement items. The split matters more than the extraction: traceability,
coverage and gap analysis all key off a stable requirement ref, so a document
that arrives as one undifferentiated blob is worth very little.

Two extraction strategies run in order:

* **Explicit refs.** If the document already numbers its requirements
  (``REQ-014``, ``FR-3.2``, ``US-101``), those are authoritative - the customer's
  identifiers must survive into the test artefacts and back out to Jira.
* **Structural inference.** Otherwise, split on headings and bullet groups and
  mint refs. Inferred refs are marked as such so nobody mistakes them for the
  customer's own numbering.
"""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

#: Matches the numbering conventions requirement documents actually use.
REF_PATTERN = re.compile(
    r"\b((?:REQ|FR|NFR|US|AC|BR|SR|UC)[-_ ]?\d+(?:\.\d+)*)\b", re.IGNORECASE
)
HEADING = re.compile(r"^(#{1,6})\s+(.+)$|^([A-Z][^\n]{3,80})\n[=-]{3,}$", re.MULTILINE)
BULLET = re.compile(r"^\s*[-*•]\s+(.+)$", re.MULTILINE)
NUMBERED = re.compile(r"^\s*(\d+(?:\.\d+)*)[.)]\s+(.+)$", re.MULTILINE)

#: Language that flags a testable obligation rather than background prose.
MODAL = re.compile(
    r"\b(shall|must|should|will|is required to|needs to|has to|can|may|"
    r"is able to|allows?|enables?|prevents?|rejects?|validates?|displays?|"
    r"returns?|supports?)\b",
    re.IGNORECASE,
)
#: A bullet that states a constraint but no modal verb ("payment is one of card,
#: paypal or credit") is still a testable obligation, so keep it (WO#9-B).
CONSTRAINT_HINT = re.compile(
    r"\b(one of|any of|either|between|from\s+\d|at least|at most|no more than|"
    r"no fewer than|up to|exactly|only|required|mandatory|is one of)\b", re.I)

NFR_MARKERS = (
    "performance", "latency", "throughput", "availability", "uptime", "security",
    "accessibility", "wcag", "gdpr", "compliance", "scalab", "concurrent",
    "response time", "encrypt", "audit", "retention", "backup",
)
RISK_MARKERS = {
    "critical": ("payment", "checkout", "password", "authentication", "authorisation",
                 "authorization", "pii", "personal data", "financial", "billing",
                 "delete", "irreversible", "compliance", "gdpr", "audit"),
    "high": ("login", "sign in", "register", "permission", "role", "security",
             "order", "submit", "transaction", "encrypt", "session"),
}
AMBIGUOUS = (
    "etc", "and/or", "as appropriate", "if necessary", "user-friendly", "fast",
    "intuitive", "reasonable", "suitable", "various", "some", "many", "tbd",
    "to be defined", "as needed", "where applicable", "robust", "seamless",
)


@dataclass(slots=True)
class ExtractedDoc:
    text: str
    page_count: int = 0
    mime_type: str = "text/plain"
    warnings: list[str] = field(default_factory=list)
    #: Requirements a structured source (a spreadsheet) already separated for
    #: us. When present these are authoritative and the prose splitter is
    #: skipped - re-deriving rows from rendered text would lose the columns.
    structured: list = field(default_factory=list)
    #: Character offset in ``text`` where each page begins (WO#9-A). Lets a
    #: requirement's char position map back to a page number for its anchor.
    #: Empty for formats without pages (Markdown, DOCX, a spreadsheet).
    page_offsets: list[int] = field(default_factory=list)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()


@dataclass(slots=True)
class CandidateRequirement:
    ref: str
    title: str
    text: str
    section: str = ""
    kind: str = "functional"
    risk: str = "medium"
    acceptance_criteria: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    inferred_ref: bool = False
    #: {heading_path:[...], page:int|None, line:int|None, char_start:int}, filled
    #: by the deterministic splitter; ``doc_id`` is added at persist time (WO#9-A).
    source_anchor: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "ref": self.ref, "title": self.title, "text": self.text,
            "section": self.section, "kind": self.kind, "risk": self.risk,
            "acceptance_criteria": self.acceptance_criteria,
            "open_questions": self.open_questions,
            "inferred_ref": self.inferred_ref,
            "source_anchor": self.source_anchor,
        }


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
def extract(data: bytes, filename: str, mime_type: str = "") -> ExtractedDoc:
    from . import spreadsheet

    suffix = Path(filename).suffix.lower()
    if spreadsheet.looks_like_spreadsheet(filename, mime_type):
        return _extract_spreadsheet(data, filename)
    # Prefer the optional layout-faithful parser (docling) for rich office formats;
    # it returns None when not installed or it can't handle the file (WO#9-A).
    if suffix in {".pdf", ".docx", ".doc", ".pptx", ".ppt"} or "wordprocessingml" in mime_type \
            or "presentationml" in mime_type or mime_type == "application/pdf":
        layout = _extract_with_docling(data, filename)
        if layout is not None:
            return layout
    if suffix == ".pdf" or mime_type == "application/pdf":
        return _extract_pdf(data)
    if suffix in {".docx", ".doc"} or "wordprocessingml" in mime_type:
        return _extract_docx(data)
    if suffix in {".pptx", ".ppt"} or "presentationml" in mime_type:
        return ExtractedDoc(
            text="", mime_type=mime_type or "application/vnd.ms-powerpoint",
            warnings=["PowerPoint decks need the optional `docling` extra for "
                      "layout-faithful text (pip install 'galeqea[docling]'), or paste "
                      "the text directly."],
        )
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"} or mime_type.startswith("image/"):
        return ExtractedDoc(
            text="",
            mime_type=mime_type or "image/*",
            warnings=[
                "Image documents need OCR. Install `pytesseract` and Tesseract, or paste "
                "the text directly - GaleQEA will not silently ingest an empty document."
            ],
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1", errors="replace")
    return ExtractedDoc(text=text, mime_type=mime_type or "text/plain")


def _extract_spreadsheet(data: bytes, filename: str) -> ExtractedDoc:
    from . import spreadsheet

    sheet = spreadsheet.extract(data, filename)
    warnings = list(sheet.warnings)
    if sheet.requirements:
        warnings.insert(
            0,
            f"Read {len(sheet.requirements)} requirement row(s) from "
            f"{', '.join(sheet.sheets_read)}.",
        )
    return ExtractedDoc(
        text=sheet.as_text(),
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        warnings=warnings,
        structured=[
            CandidateRequirement(
                ref=item.ref,
                title=item.title,
                text=item.text,
                section=item.section,
                kind=item.kind,
                # A spreadsheet's own priority column is a human judgement and
                # outranks anything inferred from the wording.
                risk=item.risk or _risk_of(item.text, item.section),
                acceptance_criteria=item.acceptance_criteria,
                open_questions=_ambiguities(item.text),
                inferred_ref=item.ref.startswith("REQ-") and not item.ref[4:].isalpha(),
                # A spreadsheet row's "anchor" is its sheet + section; it has no
                # page or line, but the heading path still points a reviewer back.
                source_anchor={"heading_path": [s for s in (item.section,) if s],
                               "page": None, "line": None, "char_start": 0},
            )
            for item in sheet.requirements
        ],
    )


def _extract_pdf(data: bytes) -> ExtractedDoc:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ExtractedDoc(
            text="", mime_type="application/pdf",
            warnings=["PDF support needs `pypdf` (pip install pypdf)"],
        )
    reader = PdfReader(io.BytesIO(data))
    pages = [page.extract_text() or "" for page in reader.pages]
    warnings: list[str] = []
    if not any(p.strip() for p in pages):
        warnings.append(
            "No text layer found - this looks like a scanned PDF. It needs OCR before "
            "requirements can be extracted."
        )
    # Remember where each page starts in the joined text so a requirement's char
    # offset can be mapped back to a page number (WO#9-A).
    sep = "\n\n"
    offsets, cursor = [], 0
    for p in pages:
        offsets.append(cursor)
        cursor += len(p) + len(sep)
    return ExtractedDoc(
        text=sep.join(pages), page_count=len(pages), page_offsets=offsets,
        mime_type="application/pdf", warnings=warnings,
    )


def _extract_docx(data: bytes) -> ExtractedDoc:
    try:
        import docx
    except ImportError:
        return ExtractedDoc(
            text="", warnings=["DOCX support needs `python-docx` (pip install python-docx)"]
        )
    document = docx.Document(io.BytesIO(data))
    blocks: list[str] = []
    for para in document.paragraphs:
        if not para.text.strip():
            continue
        style = (para.style.name or "").lower()
        if "heading" in style:
            level = "".join(ch for ch in style if ch.isdigit()) or "2"
            blocks.append(f"{'#' * int(level)} {para.text.strip()}")
        else:
            blocks.append(para.text.strip())
    # Tables in requirement documents are usually the acceptance criteria.
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                blocks.append(" | ".join(cells))
    return ExtractedDoc(
        text="\n\n".join(blocks),
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


def _extract_with_docling(data: bytes, filename: str) -> ExtractedDoc | None:
    """Layout-faithful extraction via the optional ``docling`` extra.

    Returns None when docling is not installed, or cannot handle the file, so the
    caller falls back to pypdf/python-docx. When it succeeds it exports Markdown
    (headings intact), which the deterministic splitter turns into proper heading
    paths, so provenance is *better* with docling, not different in shape (WO#9-A).
    """
    try:
        from docling.datamodel.base_models import DocumentStream
        from docling.document_converter import DocumentConverter
    except ImportError:
        return None
    try:
        stream = DocumentStream(name=filename, stream=io.BytesIO(data))
        result = DocumentConverter().convert(stream)
        markdown = result.document.export_to_markdown()
    except Exception:  # noqa: BLE001 - any docling failure degrades, never breaks ingest
        return None
    if not markdown.strip():
        return None
    pages = 0
    try:
        pages = len(getattr(result.document, "pages", []) or [])
    except Exception:  # noqa: BLE001
        pages = 0
    return ExtractedDoc(
        text=markdown, page_count=pages, mime_type="text/markdown",
        warnings=[f"Parsed with docling (layout-faithful) from {Path(filename).name}."],
    )


# --------------------------------------------------------------------------- #
# Requirement splitting (deterministic - runs with no model)
# --------------------------------------------------------------------------- #
def split_requirements(
    text: str, *, prefix: str = "REQ", page_offsets: list[int] | None = None
) -> list[CandidateRequirement]:
    if not text.strip():
        return []

    page_offsets = page_offsets or []
    sections = _sections(text)
    out: list[CandidateRequirement] = []
    seen_refs: set[str] = set()
    counter = 0

    for section in sections:
        section_title = section.heading_path[-1] if section.heading_path else ""
        for block in _statements(section.body, base_offset=section.char_start):
            chunk = block.text
            explicit = REF_PATTERN.search(chunk)
            if explicit:
                ref = explicit.group(1).upper().replace("_", "-").replace(" ", "-")
                inferred = False
            else:
                counter += 1
                ref = f"{prefix}-{counter:03d}"
                inferred = True

            base_ref = ref
            dedupe = 1
            while ref in seen_refs:
                dedupe += 1
                ref = f"{base_ref}.{dedupe}"
            seen_refs.add(ref)

            # A back-pointer to exactly where this rule was written, so a
            # generated test can send a reviewer to the source (WO#9-A).
            anchor = {
                "heading_path": section.heading_path,
                "page": _page_for(block.char_start, page_offsets),
                "line": text.count("\n", 0, block.char_start) + 1,
                "char_start": block.char_start,
            }

            out.append(CandidateRequirement(
                ref=ref,
                title=_title_of(chunk),
                text=chunk.strip()[:4000],
                section=section_title,
                kind=_kind_of(chunk, section_title),
                risk=_risk_of(chunk, section_title),
                acceptance_criteria=_criteria_of(chunk, block.bullets),
                open_questions=_ambiguities(chunk),
                inferred_ref=inferred,
                source_anchor=anchor,
            ))
    return out


def _est_tokens(text: str) -> int:
    """Rough token count (~4 chars/token) for the ~2K-token chunk guard."""
    return len(text) // 4


def _page_for(offset: int, page_offsets: list[int]) -> int | None:
    """1-based page number for a char offset; None when the source has no pages."""
    if not page_offsets:
        return None
    import bisect
    return max(1, bisect.bisect_right(page_offsets, offset))


@dataclass(slots=True)
class _Section:
    """A heading's body plus the full breadcrumb of headings above it."""

    heading_path: list[str]
    body: str
    char_start: int


def _sections(text: str) -> list[_Section]:
    """Split into sections, each carrying its full heading path (``["5. Checkout"]``
    → ``["5. Checkout", "Payment"]`` for a nested sub-heading) and the char offset
    where its body begins, so every downstream anchor can name where it came from.
    """
    matches = list(HEADING.finditer(text))
    if not matches:
        return [_Section([], text, 0)]
    out: list[_Section] = []
    if matches[0].start() > 0:
        out.append(_Section([], text[: matches[0].start()], 0))
    stack: list[tuple[int, str]] = []
    for i, match in enumerate(matches):
        if match.group(1):                      # markdown "### Title"
            level, title = len(match.group(1)), (match.group(2) or "").strip()
        else:                                   # setext "Title\n====="
            level, title = 1, (match.group(3) or "").strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        heading_path = [t for _, t in stack if t]
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.append(_Section(list(heading_path), text[match.end() : end], match.end()))
    return out


@dataclass(slots=True)
class _Block:
    """A requirement statement plus the bullets that belong to it."""

    text: str
    bullets: list[str] = field(default_factory=list)
    char_start: int = 0


#: A chunk larger than this is split at sentence boundaries (WO#9-A structural
#: chunking: keep a chunk small enough to embed and reason about cheaply).
_CHUNK_TOKEN_CAP = 2000


def _split_oversize(text: str, start: int) -> list[tuple[str, int]]:
    """Break a chunk over the token cap into ~cap-sized pieces at sentence ends."""
    if _est_tokens(text) <= _CHUNK_TOKEN_CAP:
        return [(text, start)]
    pieces: list[tuple[str, int]] = []
    buf, buf_start, cursor = "", start, 0
    for sent in re.split(r"(?<=[.!?])\s+", text):
        if buf and _est_tokens(f"{buf} {sent}") > _CHUNK_TOKEN_CAP:
            pieces.append((buf.strip(), buf_start))
            buf, buf_start = sent, start + cursor
        else:
            buf = f"{buf} {sent}".strip() if buf else sent
        cursor += len(sent) + 1
    if buf.strip():
        pieces.append((buf.strip(), buf_start))
    return pieces


def _statements(body: str, *, base_offset: int = 0) -> list[_Block]:
    """Group a section into statements with their sub-bullets attached, tracking
    each block's absolute char offset for anchoring.

    Requirement documents overwhelmingly follow one of two shapes:

      A. a sentence stating the obligation, then bullets refining it;
      B. a flat bullet list where each bullet *is* a separate obligation.

    Shape A must keep its bullets as acceptance criteria - shattering one
    obligation into five destroys the traceability the whole pipeline depends
    on. Shape B must not merge them. Indentation is what distinguishes the two,
    so it is tracked rather than guessed at. A run of table rows (``a | b | c``)
    is kept whole as one chunk rather than split row-by-row (WO#9-A).
    """
    blocks: list[_Block] = []
    current: _Block | None = None
    current_indent = -1
    current_from_prose = False
    table_lines: list[str] = []
    table_start = 0
    offset = 0

    def flush_table() -> None:
        nonlocal current, current_indent, current_from_prose
        if not table_lines:
            return
        joined = "\n".join(table_lines).strip()
        if len(joined) >= 8:
            blocks.append(_Block(text=joined, char_start=base_offset + table_start))
        current, current_indent, current_from_prose = None, -1, False
        table_lines.clear()

    for raw in body.split("\n"):
        line_start = offset
        offset += len(raw) + 1               # +1 for the "\n" that split() dropped
        line = raw.strip()
        if not line:
            flush_table()
            continue

        is_bullet = bool(BULLET.match(raw) or NUMBERED.match(raw))
        if "|" in line and len(line) > 3 and not is_bullet:
            if not table_lines:
                table_start = line_start
            table_lines.append(line)
            continue
        flush_table()

        bullet = BULLET.match(raw) or NUMBERED.match(raw)
        if bullet:
            btext = (
                bullet.group(1) if bullet.re is BULLET
                else f"{bullet.group(1)} {bullet.group(2)}"
            ).strip()
            if len(btext) < 8:
                continue
            indent = len(raw) - len(raw.lstrip())
            attaches = (
                current is not None
                and not REF_PATTERN.search(btext)
                and (current_from_prose or indent > current_indent)
            )
            if attaches:
                current.bullets.append(btext)
            else:
                current = _Block(text=btext, char_start=base_offset + line_start)
                current_indent = indent
                current_from_prose = False
                blocks.append(current)
            continue

        if len(line) < 12:
            continue
        for piece, poff in _split_oversize(line, base_offset + line_start):
            current = _Block(text=piece, char_start=poff)
            blocks.append(current)
        current_indent = -1
        current_from_prose = True

    flush_table()

    # Fall back to paragraph splitting when the section has no line structure,
    # capping each paragraph at the chunk token budget.
    if not blocks:
        cursor = 0
        for part in re.split(r"(\n\s*\n)", body):
            if part.strip() and not re.fullmatch(r"\n\s*\n", part):
                lead = len(part) - len(part.lstrip())
                start = base_offset + cursor + lead
                stripped = part.strip()
                if len(stripped) > 30:
                    for piece, poff in _split_oversize(stripped, start):
                        blocks.append(_Block(text=piece.strip(), char_start=poff))
            cursor += len(part)

    return [
        b for b in blocks
        if REF_PATTERN.search(b.text) or MODAL.search(b.text)
        or CONSTRAINT_HINT.search(b.text) or len(b.text) > 60
    ]


def strip_markdown(text: str) -> str:
    """Flatten inline markdown to plain text so a title never shows raw ``**`` etc.
    (WO#9-D cosmetic). Emphasis, code, and links become their visible text."""
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)   # [label](url) -> label
    text = re.sub(r"(\*\*|__|~~|\*|_|`)(.+?)\1", r"\2", text)  # **b** *i* `c` ~~s~~
    return re.sub(r"[*_`~]", "", text)                        # any stragglers


def _title_of(chunk: str) -> str:
    # Markdown must be flattened *before* the leading-punctuation trim - a ref
    # like "**REQ-101 - Title.**" leaves "** - Title.**" once the ref itself is
    # removed, and stripping punctuation off that (still-wrapped) string can't
    # reach the dash hiding behind the "**". Flatten first, then trim.
    cleaned = strip_markdown(REF_PATTERN.sub("", chunk)).strip(" :-–\u2014\t")
    first = re.split(r"(?<=[.!?])\s+", cleaned)[0]
    return (first[:180] + "…") if len(first) > 180 else first or cleaned[:180]


def _kind_of(chunk: str, section: str = "") -> str:
    # The section heading is often the only place the NFR nature is stated.
    lowered = f"{chunk} {section}".lower()
    if any(marker in lowered for marker in NFR_MARKERS):
        return "non_functional"
    if lowered.strip().startswith(("as a", "as an")):
        return "user_story"
    if "given" in lowered and "when" in lowered and "then" in lowered:
        return "scenario"
    return "functional"


def _risk_of(chunk: str, section: str) -> str:
    """Rate risk from the requirement's own words, in RFC 2119 order.

    Two mistakes are easy here and both were made first:

    * Folding the section heading into the keyword match made every requirement
      under a heading called "Checkout" critical - and a board where everything
      is critical conveys exactly as much as one where nothing is. The heading
      is real but weak evidence, so it can raise a requirement to `high` and no
      further; only its own text can make it `critical`.

    * Matching keywords before the modal verb rated "the order list **may** be
      sorted by date" as high, because it contains the word "order". When an
      author writes MAY they have explicitly told you the obligation is
      optional; that beats anything inferred from vocabulary.
    """
    text = chunk.lower()
    heading = section.lower()

    # 1. The author's own strength of obligation, where they stated it.
    if re.search(r"\b(may|optional|nice to have|if desired)\b", text):
        return "low"

    # 2. Consequence keywords in the requirement itself.
    for level, markers in RISK_MARKERS.items():
        if any(marker in text for marker in markers):
            return level

    # 3. The heading says this area matters, not that this obligation is the
    #    dangerous one within it - so it caps at `high`.
    if any(marker in heading for markers in RISK_MARKERS.values() for marker in markers):
        return "high"

    # 4. MUST/SHALL outrank SHOULD.
    if re.search(r"\b(must|shall|required)\b", text):
        return "high"
    return "medium"


def _criteria_of(chunk: str, bullets: list[str] | None = None) -> list[str]:
    criteria: list[str] = list(bullets or [])
    gwt = re.findall(
        r"(given[^.]{5,200}?when[^.]{5,200}?then[^.]{5,200}[.\n])", chunk, re.IGNORECASE | re.DOTALL
    )
    criteria += [g.strip().replace("\n", " ") for g in gwt]
    criteria += [
        m.group(1).strip() for m in BULLET.finditer(chunk)
        if MODAL.search(m.group(1)) and len(m.group(1)) > 15
    ]
    seen: set[str] = set()
    return [c for c in criteria if not (c.lower() in seen or seen.add(c.lower()))][:10]


def _ambiguities(chunk: str) -> list[str]:
    """Flag vague wording instead of quietly inventing a precise interpretation."""
    lowered = chunk.lower()
    found = [
        f"'{term}' is not measurable - what specifically should be verified?"
        for term in AMBIGUOUS if re.search(rf"\b{re.escape(term)}\b", lowered)
    ]
    if MODAL.search(chunk) and not re.search(r"\d", chunk) and any(
        m in lowered for m in ("fast", "quick", "performance", "timely", "responsive")
    ):
        found.append("a performance expectation is stated without a numeric threshold")
    if " and " in lowered and lowered.count(" and ") >= 3:
        found.append("this compounds several obligations - consider splitting it")
    return found[:5]


def glossary_summary(text: str, *, limit: int = 1500) -> str:
    """Distil the glossary/roles/definitions sections into a compact one-line digest,
    straight from the section text; those lines are frequently too short to survive
    the requirement filter, but they are the vocabulary every rule leans on (WO#9-A)."""
    wanted = ("glossary", "definition", "terminology", "roles", "persona", "actor")
    picks: list[str] = []
    for section in _sections(text):
        hp = " ".join(section.heading_path).lower()
        if not any(w in hp for w in wanted):
            continue
        for line in section.body.split("\n"):
            stripped = line.strip(" -*\u2022\t")
            if len(stripped) > 8:
                picks.append(" ".join(stripped.split()))
    return (" \u00b7 ".join(picks))[:limit]


def summarize(candidates: list[CandidateRequirement]) -> dict:
    by_risk: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for c in candidates:
        by_risk[c.risk] = by_risk.get(c.risk, 0) + 1
        by_kind[c.kind] = by_kind.get(c.kind, 0) + 1
    questions = sum(len(c.open_questions) for c in candidates)
    return {
        "count": len(candidates),
        "by_risk": by_risk,
        "by_kind": by_kind,
        "inferred_refs": sum(1 for c in candidates if c.inferred_ref),
        "open_questions": questions,
        "with_criteria": sum(1 for c in candidates if c.acceptance_criteria),
    }
