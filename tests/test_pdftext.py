import sqlite3
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from sed import config, db, pdftext


def make_text_pdf(path: Path, pages: list[str]):
    c = canvas.Canvas(str(path), pagesize=A4)
    for t in pages:
        y = 800
        for line in t.split("\n"):
            c.drawString(60, y, line)
            y -= 16
        c.showPage()
    c.save()


def make_scanned_pdf(path: Path, text: str):
    img = Image.new("RGB", (1240, 1754), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 40)
    except OSError:
        font = ImageFont.load_default()
    d.text((80, 120), text, fill="black", font=font)
    png = path.with_suffix(".png")
    img.save(png)
    c = canvas.Canvas(str(path), pagesize=A4)
    c.drawImage(str(png), 0, 0, width=A4[0], height=A4[1])
    c.save()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SED_DATA_ROOT", str(tmp_path / "data"))
    config.load.cache_clear()
    cfg = config.load()
    con = sqlite3.connect(tmp_path / "t.sqlite"); con.row_factory = sqlite3.Row
    db.init_db(con)
    (cfg.data_root / "pdfs").mkdir(parents=True)
    yield cfg, con
    config.load.cache_clear()


def add_order(con, oid, rel):
    con.execute("INSERT INTO orders(order_id,source_category,listing_title,html_url,pdf_path,fetch_status) "
                "VALUES(?,?,?,?,?,'pdf_fetched')", (oid, "adjudicating_officer", "t", f"u-{oid}", rel))
    con.commit()


def test_text_pdf_extracts_pages_and_fts(env):
    cfg, con = env
    make_text_pdf(cfg.data_root / "pdfs/a.pdf",
                  ["ORDER\nNoticee: Ramesh Kumar penalty of Rs. 5,00,000/-" + " filler" * 20,
                   "Page two: Regulation 4(1) of PFUTP Regulations" + " filler" * 20])
    add_order(con, "a", "pdfs/a.pdf")
    assert pdftext.extract_all(cfg, con) == {"text": 1, "scanned": 0, "failed": 0}
    o = con.execute("SELECT * FROM orders WHERE order_id='a'").fetchone()
    assert o["page_count"] == 2 and o["has_text_layer"] == 1 and o["fetch_status"] == "text_extracted"
    hits = pdftext.search(con, "PFUTP")
    assert [(h["order_id"], h["page_no"]) for h in hits] == [("a", 2)]
    # idempotent: re-extract replaces, no duplicates
    pdftext.extract_order(cfg, con, "a", "pdfs/a.pdf")
    assert con.execute("SELECT COUNT(*) FROM order_pages").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM orders_fts").fetchone()[0] == 2


def test_scanned_pdf_queued_then_ocr(env):
    cfg, con = env
    make_scanned_pdf(cfg.data_root / "pdfs/s.pdf", "PENALTY IMPOSED ON NOTICEE")
    add_order(con, "s", "pdfs/s.pdf")
    assert pdftext.extract_all(cfg, con)["scanned"] == 1
    o = con.execute("SELECT has_text_layer, ocr_status FROM orders WHERE order_id='s'").fetchone()
    assert (o[0], o[1]) == (0, "queued")
    pytest.importorskip("pytesseract")
    from sed import ocr
    assert ocr.ocr_queue(cfg, con) == {"done": 1, "failed": 0}
    assert pdftext.search(con, "penalty")
    assert con.execute("SELECT ocr_status FROM orders WHERE order_id='s'").fetchone()[0] == "done"


def test_corrupt_pdf_fails_gracefully(env):
    cfg, con = env
    (cfg.data_root / "pdfs/bad.pdf").write_bytes(b"not a pdf")
    add_order(con, "bad", "pdfs/bad.pdf")
    assert pdftext.extract_all(cfg, con)["failed"] == 1
    assert con.execute("SELECT fetch_status FROM orders WHERE order_id='bad'").fetchone()[0] == "fetch_failed"


def test_clean():
    assert pdftext.clean("a\x00b  c\r\n\n\n\nd") == "ab c\n\nd"


def test_memory_flat_on_big_pdf(env):
    import resource
    cfg, con = env
    make_text_pdf(cfg.data_root / "pdfs/big.pdf", [f"Page {i} " + "lorem ipsum " * 60 for i in range(300)])
    add_order(con, "big", "pdfs/big.pdf")
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    pdftext.extract_order(cfg, con, "big", "pdfs/big.pdf")
    grown_mb = (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before) / 1024
    assert con.execute("SELECT page_count FROM orders WHERE order_id='big'").fetchone()[0] == 300
    assert grown_mb < 150
