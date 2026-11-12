"""Step 2 — scraper for sebi.gov.in → Enforcement → Orders.

Listing contract (inspected 10 Nov 2026 via browser network tab):
  POST https://www.sebi.gov.in/sebiweb/ajax/home/getnewslistinfo.jsp   (form-encoded)
    sid=2 ssid=9 smid=<sub-category> doDirect=<page index, 0-based> fromDate/toDate=dd-mm-yyyy
  Response: "<listing html>#@#<breadcrumb html>"; hidden inputs totalpage / nextDel(=25) / nextValue;
  rows: <tr><td>Nov 06, 2026</td><td><a class='points' href=...>title [<br><a>[Corrigendum]</a>]</a></td></tr>
  href is EITHER an HTML item page (PDF in <iframe src='https://www.sebi.gov.in/web/?file=<pdf>'>)
  OR a direct .pdf link.

Resumable: progress per (category, window) is checkpointed after every page; orders are
upserted per page; every raw response is cached by sed.http.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

from . import db
from .config import Config
from .http import FetchError, Http

log = logging.getLogger(__name__)

LISTING_URL = "/sebiweb/ajax/home/getnewslistinfo.jsp"
PAGE_SIZE = 25

# smid -> (source_category, smText as the site sends it)
CATEGORIES: dict[int, tuple[str, str]] = {
    2: ("chairman_members", "Orders of Chairperson/Members"),
    6: ("adjudicating_officer", "Orders of AO"),
    3: ("settlement", "Settlement Orders passed by the Board"),
    146: ("settlement", "Settlement Orders passed by the Tribunal or Court"),
    133: ("ed_cgm", "Orders of ED / CGM (Quasi-Judicial Authorities)"),
    138: ("other", "Orders under Regulation 30A of the SEBI (Intermediaries) Regulations, 2008"),
}
HINDI_RE = re.compile(r"hindi|हिन्दी|हिंदी", re.IGNORECASE)
_DATE_FMT = "%b %d, %Y"


@dataclass
class ListingRow:
    listing_date: str | None       # ISO
    title: str
    href: str
    corrigendum_urls: list[str] = field(default_factory=list)
    is_hindi: bool = False

    @property
    def is_direct_pdf(self) -> bool:
        return urlsplit(self.href).path.lower().endswith(".pdf")


@dataclass
class ListingPage:
    rows: list[ListingRow]
    total_records: int
    total_pages: int


def order_id_for(href: str) -> str:
    return hashlib.sha1(href.strip().encode()).hexdigest()[:16]


def parse_listing_date(s: str) -> str | None:
    try:
        return datetime.strptime(s.strip(), _DATE_FMT).date().isoformat()
    except ValueError:
        return None


def parse_listing(html: str, base: str = "https://www.sebi.gov.in") -> ListingPage:
    html = html.split("#@#", 1)[0]
    soup = BeautifulSoup(html, "lxml")
    m = re.search(r"of\s+(\d+)\s+records", soup.get_text(" "))
    total = int(m.group(1)) if m else 0
    tp = soup.find("input", attrs={"name": "totalpage"})
    total_pages = int(tp["value"]) if tp and str(tp.get("value", "")).isdigit() else (
        (total + PAGE_SIZE - 1) // PAGE_SIZE)
    rows: list[ListingRow] = []
    for tr in soup.select("table tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        anchors = tds[1].find_all("a", href=True)
        if not anchors:
            continue
        # The site nests <a> inside <a> for "[Corrigendum]" links; parsers flatten that into
        # siblings, so: first anchor = the order, the rest = corrigenda.
        a = anchors[0]
        corr = [urljoin(base, x["href"]) for x in anchors[1:]]
        for x in a.find_all("a"):
            x.decompose()
        title = " ".join(a.get_text(" ").split())
        href = urljoin(base, a["href"].strip())
        rows.append(ListingRow(
            listing_date=parse_listing_date(tds[0].get_text()),
            title=title,
            href=href,
            corrigendum_urls=corr,
            is_hindi=bool(HINDI_RE.search(title) or HINDI_RE.search(href)),
        ))
    return ListingPage(rows=rows, total_records=total, total_pages=total_pages)


def extract_pdf_url(item_html: str, base: str = "https://www.sebi.gov.in") -> str | None:
    soup = BeautifulSoup(item_html, "lxml")
    for iframe in soup.find_all("iframe", src=True):
        src = iframe["src"]
        q = parse_qs(urlsplit(src).query)
        if "file" in q:
            return unquote(q["file"][0])
        if src.lower().endswith(".pdf"):
            return urljoin(base, src)
    for a in soup.find_all("a", href=True):
        if urlsplit(a["href"]).path.lower().endswith(".pdf") and "sebi_data" in a["href"]:
            return urljoin(base, a["href"])
    return None


def _fmt(d: date) -> str:
    return d.strftime("%d-%m-%Y")


def windows(start: date, end: date, days: int = 366):
    """Yearly windows [start, end] so deep pagination never exceeds ~1-2k rows per window."""
    cur = start
    while cur <= end:
        nxt = min(date(cur.year, 12, 31), end)
        yield cur, nxt
        cur = nxt + timedelta(days=1)


class Scraper:
    def __init__(self, cfg: Config, con, http: Http | None = None):
        self.cfg, self.con = cfg, con
        self.http = http or Http(cfg, con)
        self.base = cfg.scrape["base_url"]
        self.pdf_dir = cfg.path("pdfs")

    # ---------------------------------------------------------------- listing
    def fetch_listing_page(self, smid: int, page: int, frm: date | None, to: date | None,
                           use_cache: bool) -> ListingPage:
        _, sm_text = CATEGORIES[smid]
        data = {
            "nextValue": "1", "next": "n", "search": "",
            "fromDate": _fmt(frm) if frm else "", "toDate": _fmt(to) if to else "",
            "fromYear": "", "toYear": "", "deptId": "-1",
            "sid": "2", "ssid": "9", "smid": str(smid), "ssidhidden": "9", "intmid": "-1",
            "sText": "Enforcement", "ssText": "Orders", "smText": sm_text, "doDirect": str(page),
        }
        r = self.http.post(self.base + LISTING_URL, data, use_cache=use_cache, ext=".html")
        return parse_listing(r.text(), self.base)

    def count(self, smid: int, frm: date | None = None, to: date | None = None) -> int:
        return self.fetch_listing_page(smid, 0, frm, to, use_cache=False).total_records

    def _checkpoint(self, name: str) -> dict:
        row = self.con.execute("SELECT value FROM checkpoints WHERE name=?", (name,)).fetchone()
        return json.loads(row["value"]) if row else {}

    def _save_checkpoint(self, name: str, value: dict) -> None:
        self.con.execute(
            "INSERT INTO checkpoints(name,value,updated_at) VALUES(?,?,datetime('now')) "
            "ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (name, json.dumps(value)))
        self.con.commit()

    def scrape_listing(self, smid: int, frm: date, to: date, *, max_pages: int | None = None) -> dict:
        """Walk every page of one (category, window), upserting rows. Resumes from checkpoint."""
        cat, _ = CATEGORIES[smid]
        ck_name = f"listing:{smid}:{frm.isoformat()}:{to.isoformat()}"
        ck = self._checkpoint(ck_name)
        if ck.get("done"):
            return {"skipped": True, **ck}
        closed_window = to < date.today() - timedelta(days=7)
        page = int(ck.get("next_page", 0))
        stats = {"pages": 0, "rows": 0, "hindi_skipped": 0}
        total_pages = None
        while True:
            lp = self.fetch_listing_page(smid, page, frm, to, use_cache=closed_window)
            total_pages = lp.total_pages
            rows = []
            for r in lp.rows:
                if r.is_hindi and self.cfg.scrape.get("skip_hindi", True):
                    stats["hindi_skipped"] += 1
                    continue
                rows.append({
                    "order_id": order_id_for(r.href),
                    "source_category": cat,
                    "source_smid": smid,
                    "listing_title": r.title,
                    "listing_date": r.listing_date,
                    "html_url": r.href,
                    "pdf_url": r.href if r.is_direct_pdf else None,
                    "corrigendum_urls": json.dumps(r.corrigendum_urls) if r.corrigendum_urls else None,
                    "fetch_status": "html_fetched" if r.is_direct_pdf else "listed",
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                })
            # don't regress fetch_status on re-runs: only insert new / refresh listing fields
            self._upsert_listing_rows(rows)
            stats["pages"] += 1
            stats["rows"] += len(rows)
            page += 1
            self._save_checkpoint(ck_name, {"next_page": page, "total_pages": total_pages,
                                            "total_records": lp.total_records})
            log.info("listing smid=%s %s..%s page %d/%d rows=%d", smid, frm, to, page, total_pages, len(rows))
            if page >= total_pages or not lp.rows or (max_pages and stats["pages"] >= max_pages):
                break
        if page >= (total_pages or 0):
            self._save_checkpoint(ck_name, {"done": True, "next_page": page, "total_pages": total_pages,
                                            "total_records": lp.total_records})
        return stats

    def _upsert_listing_rows(self, rows: list[dict]) -> None:
        if not rows:
            return
        cols = list(rows[0].keys())
        sql = (f"INSERT INTO orders ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
               "ON CONFLICT(order_id) DO UPDATE SET listing_title=excluded.listing_title, "
               "listing_date=excluded.listing_date, corrigendum_urls=excluded.corrigendum_urls, "
               "source_smid=excluded.source_smid, updated_at=excluded.updated_at")
        with db.tx(self.con):
            self.con.executemany(sql, [tuple(r[c] for c in cols) for r in rows])

    # ------------------------------------------------------------- html → pdf
    def resolve_pdfs(self, limit: int | None = None) -> dict:
        q = "SELECT order_id, html_url FROM orders WHERE fetch_status='listed' ORDER BY listing_date DESC"
        if limit:
            q += f" LIMIT {int(limit)}"
        stats = {"resolved": 0, "no_pdf": 0, "failed": 0}
        for row in self.con.execute(q).fetchall():
            try:
                r = self.http.get(row["html_url"], ext=".html")
                pdf = extract_pdf_url(r.text(), self.base)
            except FetchError as e:
                log.error("item %s: %s", row["html_url"], e)
                self._set(row["order_id"], fetch_status="fetch_failed", extraction_notes=str(e)[:200])
                stats["failed"] += 1
                continue
            if pdf:
                self._set(row["order_id"], pdf_url=pdf, fetch_status="html_fetched",
                          language="hi" if HINDI_RE.search(pdf) else "en")
                stats["resolved"] += 1
            else:
                self._set(row["order_id"], fetch_status="fetch_failed", extraction_notes="no pdf found on item page")
                stats["no_pdf"] += 1
        return stats

    def download_pdfs(self, limit: int | None = None) -> dict:
        q = ("SELECT order_id, pdf_url, listing_date FROM orders "
             "WHERE fetch_status='html_fetched' AND pdf_url IS NOT NULL AND language='en' "
             "ORDER BY listing_date DESC")
        if limit:
            q += f" LIMIT {int(limit)}"
        stats = {"downloaded": 0, "failed": 0, "bytes": 0, "dupes": 0}
        for row in self.con.execute(q).fetchall():
            ym = (row["listing_date"] or "0000-00")[:7]
            dest = self.pdf_dir / ym / f"{row['order_id']}.pdf"
            try:
                if dest.exists():
                    sha, size = _sha256_file(dest), dest.stat().st_size
                else:
                    sha, size = self.http.download(row["pdf_url"], dest)
            except FetchError as e:
                log.error("pdf %s: %s", row["pdf_url"], e)
                self._set(row["order_id"], fetch_status="fetch_failed", extraction_notes=str(e)[:200])
                stats["failed"] += 1
                continue
            dup = self.con.execute("SELECT order_id FROM orders WHERE pdf_sha256=? AND order_id<>?",
                                   (sha, row["order_id"])).fetchone()
            if dup:
                stats["dupes"] += 1
            self._set(row["order_id"], pdf_sha256=sha, pdf_path=str(dest.relative_to(self.cfg.data_root)),
                      fetch_status="pdf_fetched",
                      extraction_notes=f"duplicate of {dup['order_id']}" if dup else None)
            stats["downloaded"] += 1
            stats["bytes"] += size
        return stats

    def _set(self, order_id: str, **fields) -> None:
        sets = ", ".join(f"{k}=?" for k in fields)
        with db.tx(self.con):
            self.con.execute(f"UPDATE orders SET {sets}, updated_at=datetime('now') WHERE order_id=?",
                             (*fields.values(), order_id))

    # ------------------------------------------------------------------ driver
    def run(self, *, smids: list[int] | None = None, frm: date | None = None, to: date | None = None,
            max_pages: int | None = None, pdf_limit: int | None = None, listing_only: bool = False) -> dict:
        frm = frm or date.fromisoformat(str(self.cfg.scrape["backfill_from"]))
        to = to or date.today()
        smids = smids or [s for s in CATEGORIES if CATEGORIES[s][0] in set(self.cfg.scrape["categories"])]
        report: dict = {"listing": {}, "resolve": None, "download": None}
        for smid in smids:
            for w0, w1 in windows(frm, to):
                try:
                    report["listing"][f"{smid}:{w0}:{w1}"] = self.scrape_listing(smid, w0, w1, max_pages=max_pages)
                except FetchError as e:
                    log.error("listing smid=%s %s..%s: %s", smid, w0, w1, e)
                    report["listing"][f"{smid}:{w0}:{w1}"] = {"error": str(e)}
        if not listing_only:
            report["resolve"] = self.resolve_pdfs(limit=pdf_limit)
            report["download"] = self.download_pdfs(limit=pdf_limit)
        return report


def _sha256_file(p) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()
