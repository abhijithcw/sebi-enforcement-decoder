"""`sed run` — the unattended cron pipeline. INCREMENTAL only:
  scrape last N days → PDFs → text → LLM extraction (capped orders/run + monthly ₹ cap) → site.
The historical backfill is deliberately NOT automatic: run `sed count`, `sed estimate`, then
`sed scrape` / `sed extract --yes` yourself so you see counts and ₹ first."""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from . import db, extract, pdftext
from .config import Config
from .scrape import Scraper
from .site import SiteBuilder

log = logging.getLogger(__name__)


def run(cfg: Config, con, *, client=None, today: date | None = None) -> dict:
    today = today or date.today()
    cron = cfg.raw.get("cron", {})
    days = int(cron.get("incremental_days", 45))
    max_llm = int(cron.get("max_llm_orders_per_run", 40))
    rep: dict = {}
    run_id = con.execute("INSERT INTO runs(stage) VALUES('run')").lastrowid
    con.commit()
    ok = True
    try:
        rep["scrape"] = Scraper(cfg, con).run(frm=today - timedelta(days=days), to=today)
        rep["text"] = pdftext.extract_all(cfg, con)
        if client is not None:
            rep["extract"] = extract.run(cfg, con, client, limit=max_llm)
        else:
            rep["extract"] = "skipped: no ANTHROPIC_API_KEY"
        rep["site"] = SiteBuilder(cfg, con).build()
    except Exception as e:                # log and surface; flock'd cron will retry tomorrow
        ok = False
        rep["error"] = repr(e)
        log.exception("pipeline failed")
    with db.tx(con):
        con.execute("UPDATE runs SET finished_at=datetime('now'), ok=?, stats_json=? WHERE run_id=?",
                    (int(ok), json.dumps(rep, default=str), run_id))
    return rep
