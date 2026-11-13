import sqlite3
from datetime import date

from sed import config, db, pipeline


def test_run_orders_stages_and_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("SED_DATA_ROOT", str(tmp_path / "d"))
    config.load.cache_clear()
    cfg = config.load()
    con = sqlite3.connect(tmp_path / "t.sqlite"); con.row_factory = sqlite3.Row
    db.init_db(con)
    calls = []
    monkeypatch.setattr(pipeline.Scraper, "run", lambda self, **kw: calls.append(("scrape", kw["frm"])) or {})
    monkeypatch.setattr(pipeline.pdftext, "extract_all", lambda *a, **k: calls.append("text") or {})
    monkeypatch.setattr(pipeline.extract, "run", lambda *a, **k: calls.append(("extract", k["limit"])) or {})
    rep = pipeline.run(cfg, con, client=object(), today=date(2026, 11, 13))
    assert [c if isinstance(c, str) else c[0] for c in calls] == ["scrape", "text", "extract"]
    assert calls[0][1] == date(2026, 9, 29) and calls[2] == ("extract", 40)
    assert "site" in rep and con.execute("SELECT ok FROM runs").fetchone()[0] == 1
    # no API key → extraction skipped, rest still runs
    calls.clear()
    assert "skipped" in pipeline.run(cfg, con, client=None, today=date(2026, 11, 13))["extract"]
    config.load.cache_clear()
