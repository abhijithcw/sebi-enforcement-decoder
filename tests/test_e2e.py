"""End to end on a REAL generated PDF: pdfium text -> extraction (fake LLM) -> verification -> store -> site.
Catches the classic failure: snippets the model copies from the prompt not matching pdfium's text."""
import sqlite3

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from sed import config, db, extract, pdftext
from sed.site import SiteBuilder
from tests.test_extract import FakeClient

LINES = [
    ["BEFORE THE ADJUDICATING OFFICER", "SECURITIES AND EXCHANGE BOARD OF INDIA",
     "Order in the matter of Foo Industries Ltd", "Date: March 3, 2024",
     "Noticee 1: Ramesh Kumar, Promoter and Managing Director of Foo Industries Ltd.", "Noticee 2: Foo Industries Ltd."] + ["Background text. " * 5] * 12,
    ["I find that the Noticees violated Regulation 4(1) of the SEBI (PFUTP) Regulations, 2003.",
     "Therefore, a penalty of Rs. 5,00,000/- (Rupees Five Lakh only) is imposed on Ramesh Kumar",
     "under Section 15HA of the SEBI Act, 1992.", "Foo Industries Ltd is warned."] + ["More text here. " * 5] * 10,
]


def pdf(path):
    c = canvas.Canvas(str(path), pagesize=A4)
    for page in LINES:
        y = 800
        for ln in page:
            c.drawString(40, y, ln)
            y -= 16
        c.showPage()
    c.save()


def test_pipeline_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("SED_DATA_ROOT", str(tmp_path / "d"))
    config.load.cache_clear()
    cfg = config.load()
    con = sqlite3.connect(tmp_path / "t.sqlite")
    con.row_factory = sqlite3.Row
    db.init_db(con)
    (cfg.data_root / "pdfs").mkdir(parents=True)
    pdf(cfg.data_root / "pdfs/x.pdf")
    con.execute("INSERT INTO orders(order_id,source_category,listing_title,html_url,pdf_path,pdf_sha256,fetch_status,listing_date)"
                " VALUES('x','adjudicating_officer','Adjudication Order in the matter of Foo','http://u','pdfs/x.pdf','sha','pdf_fetched','2024-03-04')")
    con.commit()
    assert pdftext.extract_all(cfg, con)["text"] == 1
    ev1 = {"page": 1, "snippet": "Noticee 1: Ramesh Kumar, Promoter and Managing Director of Foo Industries Ltd."}
    ev2 = {"page": 2, "snippet": "Therefore, a penalty of Rs. 5,00,000/- (Rupees Five Lakh only) is imposed on Ramesh Kumar"}
    payload = {"order_type": "adjudication", "order_date": "2024-03-03", "matter_summary": "Adjudication concerning Foo.",
               "evidence": ev1, "noticees": [
                   {"name_as_printed": "Ramesh Kumar", "role": "promoter", "outcome": "penalty", "evidence": ev1, "confidence": .9,
                    "penalties": [{"amount_as_printed": "Rs. 5,00,000/-", "statutory_provision": "Section 15HA of the SEBI Act, 1992",
                                   "evidence": ev2}]},
                   {"name_as_printed": "Foo Industries Ltd.", "role": "company", "outcome": "warning",
                    "evidence": {"page": 2, "snippet": "Foo Industries Ltd is warned."}, "confidence": .8}]}
    stats = extract.run(cfg, con, FakeClient(payload), "claude-haiku-5-5")
    assert stats["done"] == 1, con.execute("SELECT extraction_notes FROM orders").fetchone()[0]
    res = SiteBuilder(cfg, con).build()
    assert res["orders"] == 1 and res["entities"] == 2
    html = (cfg.path("site") / "o" / "x.html").read_text()
    assert "₹5,00,000" in html and "Warning" in html and "Penalty imposed" in html
    config.load.cache_clear()
