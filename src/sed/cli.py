"""`sed` command line. Stages are added as steps land:
  sed init-db            create/upgrade the SQLite schema      (step 1)
  sed schema             print the LLM JSON schema             (step 1)
  sed scrape             listing -> html -> pdf                (step 2)
  sed extract-text       pdf -> order_pages + FTS              (step 3)
  sed estimate           token count + ₹ cost before bulk run  (step 4)
  sed extract            structured LLM extraction             (step 4)
  sed build-site         static HTML + JSON search index       (step 6)
  sed run                all of the above, for cron            (step 6)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date

from . import config, db, schema


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sed", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=None, help="path to config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    sub.add_parser("schema")
    sc = sub.add_parser("scrape", help="listing -> item html -> pdf (resumable)")
    sc.add_argument("--from", dest="frm", type=date.fromisoformat, help="YYYY-MM-DD (default: config backfill_from)")
    sc.add_argument("--to", dest="to", type=date.fromisoformat)
    sc.add_argument("--smid", type=int, action="append", help="only this sub-category id (repeatable)")
    sc.add_argument("--max-pages", type=int, help="cap listing pages per window (smoke test)")
    sc.add_argument("--pdf-limit", type=int, help="cap item/PDF fetches this run")
    sc.add_argument("--listing-only", action="store_true", help="skip item pages and PDF downloads")
    ct = sub.add_parser("count", help="order counts per category for a date range (1 request each)")
    ct.add_argument("--from", dest="frm", type=date.fromisoformat)
    ct.add_argument("--to", dest="to", type=date.fromisoformat)
    et = sub.add_parser("extract-text", help="pdf -> order_pages + FTS5; scanned PDFs queued for OCR")
    et.add_argument("--limit", type=int)
    oc = sub.add_parser("ocr", help="OCR queued scanned PDFs (tesseract; run niced, low priority)")
    oc.add_argument("--limit", type=int)
    fs = sub.add_parser("fts", help="full-text search over order pages")
    fs.add_argument("query")
    es = sub.add_parser("estimate", help="token + ₹ estimate for pending orders, per model (no API calls)")
    es.add_argument("--model", action="append", help="repeatable; default: config model + Haiku 5.5")
    es.add_argument("--limit", type=int)
    ex = sub.add_parser("extract", help="LLM extraction (shows estimate; bulk runs need --yes)")
    ex.add_argument("--model")
    ex.add_argument("--limit", type=int)
    ex.add_argument("--yes", action="store_true", help="confirm spend for runs above 25 orders")
    sub.add_parser("rebuild", help="re-apply cached extractions to the tables (no API calls)")
    for name in ("build-site", "run"):
        sub.add_parser(name)
    args = p.parse_args(argv)

    if args.cmd == "schema":
        json.dump(schema.json_schema(), sys.stdout, indent=2)
        print()
        return 0

    cfg = config.load(args.config)
    if args.cmd == "init-db":
        con = db.connect(cfg)
        db.init_db(con)
        print(f"schema ready at {cfg.db_path}")
        return 0

    if args.cmd in ("scrape", "count"):
        from .scrape import CATEGORIES, Scraper
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        con = db.connect(cfg)
        db.init_db(con)
        sc = Scraper(cfg, con)
        frm = args.frm or date.fromisoformat(str(cfg.scrape["backfill_from"]))
        if args.cmd == "count":
            total = 0
            for smid, (cat, label) in CATEGORIES.items():
                n = sc.count(smid, frm, args.to or date.today())
                total += n
                print(f"{smid:>4} {cat:<20} {n:>6}  {label}")
            print(f"total since {frm}: {total} orders (~{total // 25 + 1} listing pages)")
            return 0
        rep = sc.run(smids=args.smid, frm=frm, to=args.to, max_pages=args.max_pages,
                     pdf_limit=args.pdf_limit, listing_only=args.listing_only)
        print(json.dumps(rep, indent=2, default=str))
        return 0

    if args.cmd in ("extract-text", "ocr", "fts"):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        con = db.connect(cfg)
        db.init_db(con)
        if args.cmd == "extract-text":
            from . import pdftext
            print(json.dumps(pdftext.extract_all(cfg, con, args.limit)))
        elif args.cmd == "ocr":
            from . import ocr
            print(json.dumps(ocr.ocr_queue(cfg, con, args.limit)))
        else:
            from . import pdftext
            for r in pdftext.search(con, args.query):
                print(f"{r['order_id']} p{r['page_no']}: {r['snip']}")
        return 0

    if args.cmd == "rebuild":
        from . import store
        con = db.connect(cfg)
        db.init_db(con)
        print(json.dumps(store.rebuild_from_cache(con)))
        return 0

    if args.cmd in ("estimate", "extract"):
        from . import extract
        con = db.connect(cfg)
        db.init_db(con)
        models = [getattr(args, "model", None)] if args.cmd == "extract" and args.model else (
            args.model or [cfg.llm["model"], "claude-haiku-5-5"])
        est = extract.estimate(cfg, con, models, args.limit)
        print(extract.format_estimate(est))
        if args.cmd == "estimate":
            return 0
        if est["orders"] > 25 and not args.yes:
            print("Above 25 orders: re-run with --yes to spend. Nothing sent.", file=sys.stderr)
            return 3
        if not cfg.anthropic_api_key:
            print("ANTHROPIC_API_KEY missing in .env", file=sys.stderr)
            return 4
        import anthropic
        print(json.dumps(extract.run(cfg, con, anthropic.Anthropic(api_key=cfg.anthropic_api_key),
                                     args.model, args.limit)))
        return 0

    print(f"{args.cmd}: not implemented yet (see docs/STATUS.md)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
