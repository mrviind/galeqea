"""WO#9-A: ingestion carries provenance (source_anchor) and chunks structurally."""

from __future__ import annotations

from galeqea.engine import ingest
from galeqea.intelligence import ui_inventory
from galeqea.services import requirements as req_service

_MD = """# Checkout PRD

## 1. Glossary

- **Shopper**: an authenticated end user with a cart.

## 2. Account Registration

- The password must be between 8 and 64 characters.
- The email field is required and must match a standard email format.

## 3. Checkout

- The order total must be at least $1.00 to place an order.
"""


def test_source_anchor_has_heading_breadcrumb_and_line():
    cands = ingest.split_requirements(_MD)
    by_ref = {c.title[:20]: c for c in cands}
    pw = next(c for c in cands if "password must be between" in c.text)
    a = pw.source_anchor
    assert a["heading_path"] == ["Checkout PRD", "2. Account Registration"]
    assert a["page"] is None                      # markdown has no pages
    assert a["line"] == _MD[: a["char_start"]].count("\n") + 1
    # the char offset actually points at the requirement text
    assert _MD[a["char_start"]:].lstrip("-* ").startswith("The password must be between")


def test_page_mapping_from_offsets():
    # two "pages": page 1 is chars [0, 40), page 2 is [40, end)
    text = "# A\n\n- The name must be at least 3 characters long here.\n" \
           "\n- The age must be between 18 and 99 for signup eligibility here.\n"
    split = len("# A\n\n- The name must be at least 3 characters long here.\n")
    cands = ingest.split_requirements(text, page_offsets=[0, split])
    pages = {("name" in c.text): c.source_anchor["page"] for c in cands}
    assert ingest._page_for(0, [0, split]) == 1
    assert ingest._page_for(split + 5, [0, split]) == 2
    # the second rule lives on page 2
    age = next(c for c in cands if "age must be between" in c.text)
    assert age.source_anchor["page"] == 2


def test_table_is_one_chunk():
    md = """## Pricing

| Tier | Price | Seats |
| ---- | ----- | ----- |
| Free | 0     | 1     |
| Pro  | 20    | 10    |
"""
    cands = ingest.split_requirements(md)
    table_chunks = [c for c in cands if "Tier" in c.text and "Price" in c.text]
    assert len(table_chunks) == 1                 # the whole table, not four rows
    assert "Free" in table_chunks[0].text and "Pro" in table_chunks[0].text


def test_oversize_paragraph_splits_under_token_cap():
    big = "This system shall behave correctly. " * 700   # ~4900 tokens of one paragraph
    md = f"## Big\n\n{big}"
    cands = ingest.split_requirements(md)
    assert len(cands) >= 2
    assert all(ingest._est_tokens(c.text) <= ingest._CHUNK_TOKEN_CAP + 50 for c in cands)


def test_docling_absent_falls_back():
    # docling is not installed in the test env → the loader returns None and the
    # normal pypdf/python-docx path is used.
    assert ingest._extract_with_docling(b"%PDF-1.4 fake", "x.pdf") is None


def test_ingest_persists_anchor_and_glossary(db, project):
    result = req_service.ingest_document(
        db, project_id=project.id, filename="prd.md", data=_MD.encode(),
        title="Checkout PRD",
    )
    assert result.items
    for item in result.items:
        assert item.source_anchor.get("doc_id") == result.doc.id
        assert "heading_path" in item.source_anchor
    # a glossary summary was distilled onto the doc
    assert "Shopper" in (result.doc.meta.get("glossary_summary") or "")


# --- UI inventory (deterministic parts) ------------------------------------ #

_INV = {
    "screen": "Registration",
    "controls": [
        {"label": "Email", "type": "email", "validation": "be a valid email"},
        {"label": "Password", "type": "password"},
        {"label": "Referral code", "type": "text"},   # not in the PRD → hidden requirement
    ],
    "nav_targets": ["Dashboard"],
}


def test_inventory_chunk_renders():
    chunk = ui_inventory.inventory_chunk(_INV)
    assert "UI inventory: Registration" in chunk
    assert "**Email**" in chunk and "Referral code" in chunk


def test_inventory_gaps_finds_ui_not_in_text():
    texts = ["The email field is required.", "The password must be at least 8 characters."]
    gaps = ui_inventory.inventory_gaps(_INV, texts)
    kinds = {g["kind"] for g in gaps}
    assert "ui_not_in_text" in kinds
    ui_gap = next(g for g in gaps if g["kind"] == "ui_not_in_text")
    assert "Referral code" in ui_gap["control"]


def test_inventory_gaps_empty_without_controls():
    assert ui_inventory.inventory_gaps({}, ["anything"]) == []
