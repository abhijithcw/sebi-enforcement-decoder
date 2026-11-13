"""Low-priority OCR for scanned PDFs (ocr_status='queued'). Needs `tesseract-ocr` + `pip install .[ocr]`.
Renders one page at a time with pypdfium2 (150 dpi) so memory stays flat; run it separately/at night:
    nice -n 19 sedec ocr --limit 20
"""
from __future__ import annotations

import logging

import pypdfium2 as pdfium

from .config import Config
from .pdftext import _set, clean, store_pages

log = logging.getLogger(__name__)


def ocr_order(cfg: Config, con, order_id: str, pdf_rel_path: str, dpi: int = 150) -> bool:
    try:
        import pytesseract
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("OCR needs: sudo apt install tesseract-ocr && pip install '.[ocr]'") from e
    pages: list[tuple[int, str]] = []
    pdf = pdfium.PdfDocument(str(cfg.data_root / pdf_rel_path))
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            img = page.render(scale=dpi / 72).to_pil()
            page.close()
            pages.append((i + 1, clean(pytesseract.image_to_string(img, lang="eng"))))
            img.close()
    except Exception as e:
        log.error("ocr %s: %s", order_id, e)
        _set(con, order_id, ocr_status="failed", extraction_notes=f"ocr failed: {e}"[:200])
        return False
    finally:
        pdf.close()
    store_pages(con, order_id, pages)
    _set(con, order_id, has_text_layer=1, ocr_status="done", fetch_status="text_extracted",
         extraction_notes="text via OCR (tesseract) — verify figures against the PDF")
    return True


def ocr_queue(cfg: Config, con, limit: int | None = None) -> dict:
    q = "SELECT order_id, pdf_path FROM orders WHERE ocr_status='queued' ORDER BY listing_date DESC"
    if limit:
        q += f" LIMIT {int(limit)}"
    stats = {"done": 0, "failed": 0}
    for row in con.execute(q).fetchall():
        stats["done" if ocr_order(cfg, con, row["order_id"], row["pdf_path"]) else "failed"] += 1
    return stats
