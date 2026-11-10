import sqlite3

from sed import db


def test_schema_applies_and_upserts(tmp_path):
    con = sqlite3.connect(tmp_path / "t.sqlite")
    con.row_factory = sqlite3.Row
    db.init_db(con)
    db.init_db(con)  # idempotent

    rows = [
        {"order_id": "a", "source_category": "settlement", "listing_title": "T1", "html_url": "u1"},
        {"order_id": "b", "source_category": "other", "listing_title": "T2", "html_url": "u2"},
    ]
    assert db.upsert_many(con, "orders", rows, ["order_id"], batch_size=1) == 2
    rows[0]["listing_title"] = "T1-updated"
    db.upsert_many(con, "orders", rows, ["order_id"])
    assert con.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 2
    assert con.execute("SELECT listing_title FROM orders WHERE order_id='a'").fetchone()[0] == "T1-updated"

    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("orders", "entities", "noticees", "penalties", "violations", "appeals", "order_pages", "llm_cache"):
        assert t in tables
