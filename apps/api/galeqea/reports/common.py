"""Shared building blocks for AI-readable reports (JSON / Markdown / JUnit).

Every report exists in three shapes for three readers:

* **JSON** for machines: a stable ``schema_version``, stable field names, and
  every item carrying ``id``, ``href`` (where to fetch it over the API) and
  ``ui_href`` (where a human sees it). An external agent can traverse it.
* **Markdown** for LLMs: an H1, a five-line executive summary, then tables, and a
  hard token budget so it drops into a context window whole, with a pointer to the
  full JSON when it has to truncate.
* **JUnit XML** (runs only) for CI: the lingua franca every pipeline already reads.

The builders here keep the three consistent: one source of truth per report,
three renderings. Everything is deterministic; the same state renders identically,
so a diff between two reports is a real change, not noise.
"""

from __future__ import annotations

import re
from xml.sax.saxutils import escape as _sax_escape
from xml.sax.saxutils import quoteattr as _sax_quoteattr

#: Bumped when a JSON shape changes in a way a consumer could break on. Consumers
#: should read it and refuse a major they don't understand.
SCHEMA_VERSION = "1.0"

#: XML 1.0 forbids most C0 control characters (everything except tab/LF/CR); both
#: the Word and Excel writers raise outright the moment one reaches a run/cell,
#: which a raw browser/Playwright error message (terminal color codes, a stray
#: NUL from a binary response body) triggers often enough to be a real bug, not
#: a hypothetical one. Strip them once, here, so every report format is safe.
_XML_ILLEGAL_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


def strip_control_chars(value: str) -> str:
    """Remove characters XML (and therefore .docx/.xlsx) cannot carry, keeping
    tab/newline/carriage-return intact since those render fine and matter for
    a multi-line error message's readability."""
    return _XML_ILLEGAL_RE.sub("", value)


def donut_svg(segments: list[tuple[str, int, str]], *, size: int = 160,
             stroke: int = 22, center_value: str = "", center_sub: str = "",
             ink: str = "#e6edf3", muted: str = "#9aa4b2") -> str:
    """An inline, dependency-free SVG doughnut for a self-contained HTML report
    (no chart JS, no external assets, so it still renders in a downloaded file
    opened offline or a stakeholder-mode share page with everything stripped).
    One `<circle>` per segment, positioned with `stroke-dasharray` /
    `stroke-dashoffset` on a circle of known circumference; `segments` with a
    zero count are skipped in the ring but still expected in the legend the
    caller renders separately (the legend, not this function, lists them)."""
    r = (size - stroke) / 2
    cx = cy = size / 2
    circumference = 2 * 3.14159265358979 * r
    total = sum(max(0, n) for _, n, _ in segments)
    arcs = []
    offset = 0.0
    if total > 0:
        for _, n, color_hex in segments:
            if n <= 0:
                continue
            frac = n / total
            length = circumference * frac
            arcs.append(
                f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#{color_hex}" '
                f'stroke-width="{stroke}" stroke-dasharray="{length:.2f} {circumference - length:.2f}" '
                f'stroke-dashoffset="{-offset:.2f}" transform="rotate(-90 {cx} {cy})" />')
            offset += length
    else:
        arcs.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#3a3f4b" '
                    f'stroke-width="{stroke}" />')
    center = ""
    if center_value:
        center = (f'<text x="{cx}" y="{cy - (2 if center_sub else -4)}" text-anchor="middle" '
                 f'font-size="{size * 0.155:.0f}" font-weight="700" fill="{ink}">{_sax_escape(center_value)}</text>')
        if center_sub:
            center += (f'<text x="{cx}" y="{cy + size * 0.13:.0f}" text-anchor="middle" '
                       f'font-size="{size * 0.075:.0f}" fill="{muted}" '
                       f'letter-spacing="0.5">{_sax_escape(center_sub.upper())}</text>')
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" '
           f'role="img" aria-label="{_sax_quoteattr(center_sub or "results")}">' +
           "".join(arcs) + center + "</svg>")

#: Markdown is sized for an LLM context window: a rough 4 chars/token, capped so a
#: report is a cheap thing to hand a model. Over budget, tables are truncated and a
#: "full data" pointer to the JSON is appended, never a silent cut.
_CHARS_PER_TOKEN = 4
MD_TOKEN_BUDGET = 4000


def api_href(project_id: str, *parts: object) -> str:
    """Canonical API path for a resource, e.g. ``/api/projects/{pid}/runs/{id}``."""
    tail = "/".join(str(p).strip("/") for p in parts if p is not None and str(p) != "")
    return f"/api/projects/{project_id}" + (f"/{tail}" if tail else "")


def ui_href(*parts: object) -> str:
    """Path a human opens in the UI, e.g. ``/runs/{id}``."""
    tail = "/".join(str(p).strip("/") for p in parts if p is not None and str(p) != "")
    return "/" + tail


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def md_table(headers: list[str], rows: list[list[object]]) -> str:
    """A GitHub-flavoured Markdown table. Empty rows render as a single em dash so
    the section still reads as 'nothing here', not a broken table."""
    if not rows:
        return "_None._\n"
    head = "| " + " | ".join(headers) + " |"
    rule = "| " + " | ".join("---" for _ in headers) + " |"
    body = "\n".join(
        "| " + " | ".join(_md_cell(c) for c in row) + " |" for row in rows
    )
    return f"{head}\n{rule}\n{body}\n"


def _md_cell(value: object) -> str:
    # Pipes and newlines would break the table grid.
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def cap_markdown(markdown: str, full_href: str, budget: int = MD_TOKEN_BUDGET) -> str:
    """Keep a Markdown report within the token budget, appending an explicit
    pointer to the full JSON when it has to cut. Cuts on a line boundary."""
    if estimate_tokens(markdown) <= budget:
        return markdown
    limit_chars = budget * _CHARS_PER_TOKEN
    clipped = markdown[:limit_chars]
    clipped = clipped[: clipped.rfind("\n")] if "\n" in clipped else clipped
    return clipped.rstrip() + (
        f"\n\n_Truncated to fit an LLM context window. "
        f"Full data (JSON): {full_href}_\n"
    )


def xml_text(value: object) -> str:
    return _sax_escape(str(value if value is not None else ""))


def xml_attr(value: object) -> str:
    """A fully quoted, escaped XML attribute value (includes the quotes)."""
    return _sax_quoteattr(str(value if value is not None else ""))


def envelope(project_id: str, kind: str, self_parts: tuple, extra: dict) -> dict:
    """The common top of every JSON report: schema version, what it is, and where
    it lives (API + UI). Report-specific fields go in ``extra``."""
    return {
        "schema_version": SCHEMA_VERSION,
        "report": kind,
        "project_id": project_id,
        "href": api_href(project_id, *self_parts),
        **extra,
    }
