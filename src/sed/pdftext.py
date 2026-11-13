"""Step 3 — PDF → page-wise text → order_pages + FTS5.

* pypdfium2 (Apache/BSD) page-by-page; each page + textpage closed immediately, so RSS stays flat
  regardless of PDF size. PyMuPDF deliberately not used (AGPL).
* Scanned detection: if fewer than half the pages carry >= min_chars_per_page_for_text_layer
  characters, the PDF is treated as image-only → has_text_layer=0, ocr_status='queued'. OCR itself
  (sed.ocr, tesseract) runs separately at low priority.
* Idempotent: re-extracting an order replaces its pages and FTS rows (FTS rowid == order_pages rowid).
"""
from __future__ import annotations

import gc
import logging
import re
from collections.abc import Iterator

import pypdfium2 as pdfium

from . import db
from .config import Config

log = logging.getLogger(__name__)

_NUL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_SPACES_RE = re.compile(r"[ \t ]+")
_BLANKS_RE = re.compile(r"\n{3,}")


def clean(text: str) -> str:
    text = _NUL_RE.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = _SPACES_RE.sub(" ", text)
    return _BLANKS_RE.sub("\n\n", text).strip()


def iter_pages(pdf_path) -> Iterator[tuple[int, str]]:
    """Yield (1-based page number, cleaned text) one page at a time."""
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            tp = page.get_textpage()
            try:
                yield i + 1, clean(tp.get_text_range())
            finally:
                tp.close()
                page.close()
            if i % 50 == 49:
                gc.collect()
    finally:
        pdf.close()


_DEVANAGARI_RE = re.compile(r"[\u0900-\u097f]")
_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)


def devanagari_ratio(pages: list[tuple[int, str]]) -> float:
    """Share of letters that are Devanagari — catches Hindi copies the listing didn't label."""
    text = " ".join(t for _, t in pages)
    letters = len(_LETTER_RE.findall(text))
    return len(_DEVANAGARI_RE.findall(text)) / letters if letters else 0.0


def has_text_layer(char_counts: list[int], min_chars: int) -> bool:
    if not char_counts:
        return False
    good = sum(1 for c in char_counts if c >= min_chars)
    return good / len(char_counts) >= 0.5


def store_pages(con, order_id: str, pages: list[tuple[int, str]]) -> None:
    """Replace this order's pages + FTS rows in one transaction."""
    with db.tx(con):
        old = [r[0] for r in con.execute("SELECT rowid FROM order_pages WHERE order_id=?", (order_id,))]
        if old:
            con.executemany("DELETE FROM orders_fts WHERE rowid=?", [(r,) for r in old])
            con.execute("DELETE FROM order_pages WHERE order_id=?", (order_id,))
        for page_no, text in pages:
            cur = con.execute("INSERT INTO order_pages(order_id,page_no,text,char_count) VALUES(?,?,?,?)",
                              (order_id, page_no, text, len(text)))
            con.execute("INSERT INTO orders_fts(rowid,order_id,page_no,text) VALUES(?,?,?,?)",
                        (cur.lastrowid, order_id, page_no, text))


def extract_order(cfg: Config, con, order_id: str, pdf_rel_path: str) -> str:
    """Returns 'text', 'scanned', 'hindi' or 'failed'."""
    min_chars = int(cfg.pdf["min_chars_per_page_for_text_layer"])
    path = cfg.data_root / pdf_rel_path
    try:
        pages = list(iter_pages(path))      # one order's text is small (KBs-MBs); pages stream out of pdfium
    except Exception as e:                  # pdfium raises PdfiumError / OSError on corrupt files
        log.error("pdf %s: %s", order_id, e)
        _set(con, order_id, fetch_status="fetch_failed", extraction_notes=f"pdf unreadable: {e}"[:200])
        return "failed"
    counts = [len(t) for _, t in pages]
    if devanagari_ratio(pages) > 0.3:       # Hindi duplicate: skip, never stored, never sent to the LLM
        _set(con, order_id, page_count=len(pages), language="hi", has_text_layer=1, ocr_status="not_needed",
             fetch_status="text_extracted", extraction_status="skipped",
             extraction_notes="Hindi duplicate (Devanagari text) — skipped")
        return "hindi"
    if has_text_layer(counts, min_chars):
        store_pages(con, order_id, pages)
        _set(con, order_id, page_count=len(pages), has_text_layer=1, ocr_status="not_needed",
             fetch_status="text_extracted")
        return "text"
    _set(con, order_id, page_count=len(pages), has_text_layer=0, ocr_status="queued",
         extraction_notes="scanned PDF (no text layer) — queued for OCR")
    return "scanned"


def extract_all(cfg: Config, con, limit: int | None = None) -> dict:
    q = ("SELECT order_id, pdf_path FROM orders WHERE fetch_status='pdf_fetched' AND pdf_path IS NOT NULL "
         "AND (has_text_layer IS NULL) ORDER BY listing_date DESC")
    if limit:
        q += f" LIMIT {int(limit)}"
    stats = {"text": 0, "scanned": 0, "failed": 0, "hindi": 0}
    for row in con.execute(q).fetchall():
        stats[extract_order(cfg, con, row["order_id"], row["pdf_path"])] += 1
    return stats


def _set(con, order_id: str, **fields) -> None:
    sets = ", ".join(f"{k}=?" for k in fields)
    with db.tx(con):
        con.execute(f"UPDATE orders SET {sets}, updated_at=datetime('now') WHERE order_id=?",
                    (*fields.values(), order_id))


def search(con, query: str, limit: int = 20):
    """FTS5 full-text search; returns (order_id, page_no, snippet)."""
    return con.execute(
        "SELECT order_id, page_no, snippet(orders_fts, 2, '[', ']', ' … ', 12) AS snip "
        "FROM orders_fts WHERE orders_fts MATCH ? ORDER BY rank LIMIT ?", (query, limit)).fetchall()
