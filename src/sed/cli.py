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
import sys

from . import config, db, schema


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sed", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=None, help="path to config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    sub.add_parser("schema")
    for name in ("scrape", "extract-text", "estimate", "extract", "build-site", "run"):
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

    print(f"{args.cmd}: not implemented yet (see docs/STATUS.md)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
