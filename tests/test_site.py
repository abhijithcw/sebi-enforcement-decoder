import json
import re
import sqlite3

import pytest

from sed import config, db, store
from sed.site import SiteBuilder
from tests.test_store import order  # noqa

LEGAL = "Indicators derived from public filings; not an allegation, investment advice, or legal opinion."


@pytest.fixture
def built(tmp_path, monkeypatch):
    monkeypatch.setenv("SED_DATA_ROOT", str(tmp_path / "d"))
    config.load.cache_clear()
    cfg = config.load()
    con = sqlite3.connect(tmp_path / "t.sqlite"); con.row_factory = sqlite3.Row
    db.init_db(con)
    for i, ttl in (("o1", "Adjudication Order in the matter of Foo Ltd"), ("o2", "Interim order in the matter of Bar"),
                   ("o3", "Settlement order in respect of Ramesh Kumar"), ("o4", "Pending extraction")):
        con.execute("INSERT INTO orders(order_id,source_category,listing_title,html_url,pdf_url,listing_date) VALUES(?,?,?,?,?,?)",
                    (i, "adjudicating_officer", ttl, "https://sebi/" + i, "https://sebi/" + i + ".pdf", "2024-03-04"))
    store.apply_order(con, "o1", order(), "m", "extract_v1")                          # SAT set aside
    store.apply_order(con, "o2", order(order_type="interim", appeal_mentioned="none"), "m", "extract_v1")
    store.apply_order(con, "o3", order(order_type="settlement", appeal_mentioned="none"), "m", "extract_v1")
    con.commit()
    res = SiteBuilder(cfg, con).build()
    yield cfg.path("site"), res
    config.load.cache_clear()


def test_pages_and_legal(built):
    site, res = built
    assert res["orders"] == 3 and (site / "o" / "o1.html").exists() and not (site / "o" / "o4.html").exists()
    for f in site.rglob("*.html"):
        html = f.read_text()
        assert LEGAL in html, f
        assert 'name="viewport"' in html
        assert "violator" not in html.lower()
        assert "ABCDE1234F" not in html


def test_banners_and_strike(built):
    site, _ = built
    o1, o2, o3 = [(site / "o" / f"o{i}.html").read_text() for i in (1, 2, 3)]
    assert 'class="struck"' in o1 and "set this order aside" in o1 and "sat.gov.in" in o1
    assert "Interim — not a final finding." in o2 and "Interim — not a final finding." not in o3
    assert "Settled without admission or denial of guilt." in o3
    assert "₹10,00,000" in o1 and "jointly and severally" in o1


def test_index_and_entity(built):
    site, _ = built
    idx = json.loads((site / "search-index.json").read_text())
    assert {"e", "o", "v"} <= idx.keys() and "ABCDE1234F" not in json.dumps(idx)
    assert any("Ramesh Kumar" in e[1] for e in idx["e"])
    ent = next(site.glob("e/*.html")).read_text()
    assert "Orders naming this" in ent
    assert (site / "minisearch.js").exists() and (site / "y" / "2024.html").exists()


def test_mobile_css(built):
    css = (built[0] / "style.css").read_text()
    assert "min-height:44px" in css and "overflow-x:hidden" in css
