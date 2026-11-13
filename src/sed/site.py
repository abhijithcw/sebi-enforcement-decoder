"""Step 6 — static, mobile-first site + compact JSON search index. Regenerated every run.

Only orders with extraction_status='done' are published (needs_review stays private).
Legal display rules live HERE, in one place:
  settlement  -> "Settled without admission or denial of guilt."
  interim/ex-parte -> "Interim — not a final finding."
  SAT set aside -> title struck through + link to the SAT order (if known) or sat.gov.in
  never the word "violator"; no aggregate penalty totals (joint-and-several + set-aside make sums misleading)
  PAN masked (first 3 + last 1)
Output is built in a sibling temp dir and swapped in atomically so nginx/Caddy never serve a half-built site.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .amounts import format_inr
from .config import REPO_ROOT, Config
from .entity import mask_pan

WEB = REPO_ROOT / "web"
SITE_NAME = "SEBI Order Decoder"

TYPE_LABEL = {"interim": "Interim / ex-parte", "confirmatory": "Confirmatory", "final": "Final order",
              "settlement": "Settlement", "adjudication": "Adjudication", "revocation": "Revocation", "other": "Order"}
OUTCOME_LABEL = {"penalty": "Penalty imposed", "directions": "Directions issued", "disgorgement": "Disgorgement directed",
                 "warning": "Warning", "disposed": "Proceedings disposed", "settled": "Settled", "unknown": "Outcome not determined"}
FINDING_LABEL = {"alleged": "Alleged in order", "established": "Finding recorded in order",
                 "not_established": "Not established in order", "settled": "Settled — no finding"}
ROLE_LABEL = {"company": "Company", "promoter": "Promoter", "director": "Director", "kmp": "Key managerial person",
              "broker": "Broker / intermediary", "other": "Other"}
APPEAL_LABEL = {"none_known": "No appeal known", "appealed_to_sat": "Appealed to SAT — outcome not recorded here",
                "sat_set_aside": "Set aside by SAT", "sat_modified": "Modified by SAT", "sat_upheld": "Upheld by SAT"}


def slug(entity_id: str) -> str:
    return hashlib.sha1(entity_id.encode()).hexdigest()[:12]


def fmt_date(iso: str | None) -> str:
    if not iso:
        return "date n/a"
    try:
        return date.fromisoformat(iso[:10]).strftime("%d %b %Y").lstrip("0")
    except ValueError:
        return iso


def _months(m: int | None) -> str | None:
    if not m:
        return None
    y, r = divmod(m, 12)
    return " ".join(filter(None, [f"{y} yr" if y else "", f"{r} mo" if r else ""]))


def order_view(row, con=None) -> dict:
    t = row["order_type"]
    st = row["appeal_status"]
    sat_url = None
    if con is not None and st != "none_known":
        r = con.execute("SELECT appeal_order_url FROM appeals WHERE order_id=? AND appeal_order_url IS NOT NULL LIMIT 1",
                        (row["order_id"],)).fetchone()
        sat_url = r[0] if r else None
    sat_note = {"sat_set_aside": "The Securities Appellate Tribunal set this order aside.",
                "sat_modified": "The Securities Appellate Tribunal modified this order."}.get(st)
    return {
        "id": row["order_id"], "title": " ".join((row["listing_title"] or "").split()), "type_label": TYPE_LABEL.get(t, "Order"),
        "interim": t == "interim" or bool(row["is_ex_parte"]), "settlement": t == "settlement",
        "struck": st == "sat_set_aside", "sat_note": sat_note, "sat_url": sat_url,
        "sat_badge": "Set aside by SAT" if st == "sat_set_aside" else "Modified by SAT",
        "date": fmt_date(row["order_date"] or row["listing_date"]), "authority": row["issuing_authority"],
        "signatory": row["signatory"], "ref": row["sebi_ref"], "appeal_label": APPEAL_LABEL.get(st, st),
        "summary": row["matter_summary"], "html_url": row["html_url"], "pdf_url": row["pdf_url"],
        "model": row["extraction_model"], "prompt_version": row["prompt_version"], "entity_outcome": None,
    }


class SiteBuilder:
    def __init__(self, cfg: Config, con):
        self.cfg, self.con = cfg, con
        self.env = Environment(loader=FileSystemLoader(WEB / "templates"), autoescape=select_autoescape(["html"]))
        self.legal = cfg.legal
        self.built = datetime.now().strftime("%d %b %Y %H:%M IST")

    def _render(self, tpl: str, out: Path, root: str, title: str, **ctx) -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        html = self.env.get_template(tpl).render(
            root=root, title=title, site_name=SITE_NAME, legal_footer=self.legal["footer"], built=self.built,
            interim_banner=self.legal["interim_banner"], settlement_banner=self.legal["settlement_banner"], **ctx)
        out.write_text(html, encoding="utf-8")

    # ---------------------------------------------------------------- pages
    def order_pages(self, out: Path) -> None:
        for row in self.con.execute("SELECT * FROM orders WHERE extraction_status='done' ORDER BY order_id"):
            o = order_view(row, self.con)
            nots = []
            for n in self.con.execute(
                    "SELECT n.*, e.pan AS e_pan, e.entity_id AS eid FROM noticees n LEFT JOIN entities e "
                    "ON e.entity_id=n.entity_id WHERE n.order_id=? ORDER BY n.noticee_no, n.noticee_id", (row["order_id"],)):
                pens = [{"amount": format_inr(p["amount_inr"]), "kind": p["kind"].replace("_", " "),
                         "provision": p["statutory_provision"], "joint": bool(p["joint_and_several"]),
                         "group": p["joint_group"], "page": p["page_ref"], "snippet": p["snippet"]}
                        for p in self.con.execute("SELECT * FROM penalties WHERE noticee_id=?", (n["noticee_id"],))]
                viol = [{"regulation": v["regulation"], "clause": v["clause"], "finding": FINDING_LABEL.get(v["finding"])}
                        for v in self.con.execute("SELECT * FROM violations WHERE noticee_id=?", (n["noticee_id"],))]
                nots.append({"name": n["name_as_printed"], "slug": slug(n["eid"]) if n["eid"] else None,
                             "role": ROLE_LABEL.get(n["role"], n["role"]), "role_detail": n["role_detail"],
                             "pan": mask_pan(n["e_pan"]), "outcome": OUTCOME_LABEL.get(n["outcome"], n["outcome"]),
                             "outcome_detail": n["outcome_detail"], "debarment": _months(n["debarment_months"]),
                             "penalties": pens, "violations": viol, "page": n["page_ref"], "snippet": n["snippet"]})
            o["noticees"] = nots
            o["order_violations"] = [{"regulation": v["regulation"], "clause": v["clause"],
                                      "finding": FINDING_LABEL.get(v["finding"]), "page": v["page_ref"], "snippet": v["snippet"]}
                                     for v in self.con.execute("SELECT * FROM violations WHERE order_id=? AND noticee_id IS NULL",
                                                               (row["order_id"],))]
            self._render("order.html", out / "o" / f"{row['order_id']}.html", "../", o["title"], o=o)

    def entity_pages(self, out: Path) -> int:
        n = 0
        for e in self.con.execute("SELECT * FROM entities ORDER BY entity_id"):
            rows = self.con.execute(
                "SELECT o.*, GROUP_CONCAT(DISTINCT n.outcome) AS outs FROM noticees n JOIN orders o ON o.order_id=n.order_id "
                "WHERE n.entity_id=? AND o.extraction_status='done' GROUP BY o.order_id "
                "ORDER BY COALESCE(o.order_date,o.listing_date) DESC", (e["entity_id"],)).fetchall()
            if not rows:
                continue
            views = []
            for r in rows:
                v = order_view(r, self.con)
                v["entity_outcome"] = ", ".join(OUTCOME_LABEL.get(x, x) for x in sorted((r["outs"] or "").split(",")))
                views.append(v)
            aliases = [a[0] for a in self.con.execute(
                "SELECT alias_raw FROM entity_aliases WHERE entity_id=? AND alias_raw<>? ORDER BY seen_count DESC LIMIT 8",
                (e["entity_id"], e["canonical_name"]))]
            res = {"cin_printed": "CIN printed in an order", "isin_printed": "ISIN", "pan_printed": "PAN printed in an order",
                   "name_exact": "name only"}.get(e["resolution_method"])
            ev = {"name": e["canonical_name"], "kind": e["entity_kind"], "pan": mask_pan(e["pan"]), "cin": e["cin"],
                  "isin": e["isin"], "aliases": aliases, "resolution": res, "summary": None}
            self._render("entity.html", out / "e" / f"{slug(e['entity_id'])}.html", "../", e["canonical_name"], e=ev, orders=views)
            n += 1
        return n

    def year_pages(self, out: Path) -> list[int]:
        years = [int(r[0]) for r in self.con.execute(
            "SELECT DISTINCT substr(COALESCE(order_date,listing_date),1,4) FROM orders WHERE extraction_status='done' "
            "AND COALESCE(order_date,listing_date) IS NOT NULL ORDER BY 1 DESC")]
        for y in years:
            rows = self.con.execute("SELECT * FROM orders WHERE extraction_status='done' AND substr(COALESCE(order_date,listing_date),1,4)=? "
                                    "ORDER BY COALESCE(order_date,listing_date) DESC", (str(y),)).fetchall()
            self._render("year.html", out / "y" / f"{y}.html", "../", f"Orders in {y}", year=y,
                         orders=[order_view(r, self.con) for r in rows])
        return years

    def index_json(self, out: Path) -> dict:
        ents = []
        for e in self.con.execute(
                "SELECT e.entity_id, e.canonical_name, e.entity_kind, COUNT(DISTINCT n.order_id) c FROM entities e "
                "JOIN noticees n ON n.entity_id=e.entity_id JOIN orders o ON o.order_id=n.order_id AND o.extraction_status='done' "
                "GROUP BY e.entity_id"):
            al = " ".join(a[0] for a in self.con.execute(
                "SELECT alias_raw FROM entity_aliases WHERE entity_id=? AND alias_raw<>? LIMIT 6", (e["entity_id"], e["canonical_name"])))
            ents.append([slug(e["entity_id"]), e["canonical_name"], al, e["entity_kind"], e["c"]])
        ords = [[r["order_id"], " ".join((r["listing_title"] or "").split()), r["sebi_ref"] or "",
                 fmt_date(r["order_date"] or r["listing_date"]), TYPE_LABEL.get(r["order_type"], "")]
                for r in self.con.execute("SELECT * FROM orders WHERE extraction_status='done'")]
        idx = {"v": 1, "built": self.built, "e": ents, "o": ords}          # no PAN anywhere in the index
        (out / "search-index.json").write_text(json.dumps(idx, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        return {"entities": len(ents), "orders": len(ords)}

    def build(self) -> dict:
        final = self.cfg.path("site")
        tmp = final.with_name(final.name + ".new")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        shutil.copy(WEB / "static" / "style.css", tmp / "style.css")
        shutil.copy(WEB / "static" / "app.js", tmp / "app.js")
        shutil.copy(WEB / "vendor" / "minisearch.umd.js", tmp / "minisearch.js")
        self.order_pages(tmp)
        n_ent = self.entity_pages(tmp)
        years = self.year_pages(tmp)
        stats = self.index_json(tmp)
        latest = [order_view(r, self.con) for r in self.con.execute(
            "SELECT * FROM orders WHERE extraction_status='done' ORDER BY COALESCE(order_date,listing_date) DESC LIMIT 30")]
        self._render("index.html", tmp / "index.html", "", SITE_NAME, stats=stats, years=years, latest=latest)
        old = final.with_name(final.name + ".old")
        shutil.rmtree(old, ignore_errors=True)
        if final.exists():
            final.rename(old)
        tmp.rename(final)
        shutil.rmtree(old, ignore_errors=True)
        return {**stats, "entity_pages": n_ent, "years": years}
