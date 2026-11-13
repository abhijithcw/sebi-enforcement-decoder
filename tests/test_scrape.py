import json
import sqlite3
from datetime import date
from pathlib import Path

from sed import db
from sed.scrape import (
    CATEGORIES,
    Scraper,
    extract_pdf_url,
    order_id_for,
    parse_listing,
    parse_listing_date,
    windows,
)

FX = Path(__file__).parent / "fixtures"


def test_parse_listing():
    lp = parse_listing((FX / "listing_page.html").read_text() + "#@# Home &raquo; Orders")
    assert lp.total_records == 12023 and lp.total_pages == 481
    assert len(lp.rows) == 4
    r0, r1, r2, r3 = lp.rows
    assert r0.listing_date == "2026-11-06" and r0.title.startswith("Adjudication Order in the matter of Som")
    assert not r0.is_direct_pdf and not r0.is_hindi
    assert r1.is_direct_pdf
    assert r2.title == "Adjudication Order in respect of Sonia Chadha"          # corrigendum stripped from title
    assert r2.corrigendum_urls == ["https://www.sebi.gov.in/sebi_data/commondocs/jul-2026/Sonia%20Chadha_p.PDF"]
    assert r3.is_hindi


def test_extract_pdf_url():
    assert extract_pdf_url((FX / "item_page.html").read_text()) == \
        "https://www.sebi.gov.in/sebi_data/attachdocs/oct-2026/ORDER_1791957080.pdf"
    assert extract_pdf_url("<html></html>") is None


def test_helpers():
    assert parse_listing_date("Nov 06, 2026") == "2026-11-06"
    assert parse_listing_date("garbage") is None
    assert order_id_for("u") == order_id_for(" u ") and len(order_id_for("u")) == 16
    w = list(windows(date(2015, 1, 1), date(2016, 6, 30)))
    assert w == [(date(2015, 1, 1), date(2015, 12, 31)), (date(2016, 1, 1), date(2016, 6, 30))]
    assert {v[0] for v in CATEGORIES.values()} <= {"chairman_members", "adjudicating_officer",
                                                    "settlement", "ed_cgm", "other"}


class FakeScraper(Scraper):
    def __init__(self, con, cfg):
        self.cfg, self.con = cfg, con
        self.base = "https://www.sebi.gov.in"
        self.pages = 0

    def fetch_listing_page(self, smid, page, frm, to, use_cache):
        self.pages += 1
        lp = parse_listing((FX / "listing_page.html").read_text())
        lp.total_pages = 2
        return lp


def test_scrape_listing_idempotent_and_resumable(tmp_path):
    from sed import config
    cfg = config.load()
    con = sqlite3.connect(tmp_path / "t.sqlite"); con.row_factory = sqlite3.Row
    db.init_db(con)
    s = FakeScraper(con, cfg)
    st = s.scrape_listing(6, date(2026, 1, 1), date(2026, 12, 31))
    assert st["pages"] == 2 and st["hindi_skipped"] == 2
    n = con.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    assert n == 3                                                  # hindi skipped, same rows dedupe across pages
    row = con.execute("SELECT * FROM orders WHERE pdf_url IS NOT NULL").fetchone()
    assert row["fetch_status"] == "html_fetched" and row["source_category"] == "adjudicating_officer"
    assert json.loads(con.execute("SELECT corrigendum_urls FROM orders WHERE corrigendum_urls IS NOT NULL")
                      .fetchone()[0])
    pages_before = s.pages
    assert s.scrape_listing(6, date(2026, 1, 1), date(2026, 12, 31))["skipped"]   # checkpoint: done
    assert s.pages == pages_before


def test_circuit_breaker_stops_after_repeated_failures(tmp_path):
    from sed import config
    from sed.http import FetchError
    cfg = config.load()
    con = sqlite3.connect(tmp_path / "t.sqlite"); con.row_factory = sqlite3.Row
    db.init_db(con)
    calls = []

    class Down(FakeScraper):
        def fetch_listing_page(self, *a, **k):
            calls.append(1)
            raise FetchError("down")
    rep = Down(con, cfg).run(frm=date(2015, 1, 1), to=date(2026, 11, 1))
    assert "unreachable" in rep["aborted"] and len(calls) == 2 and rep["download"] is None
