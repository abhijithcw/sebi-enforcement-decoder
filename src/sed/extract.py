"""Step 4 — structured LLM extraction.

Rules enforced here (not just prompted):
* Only relevant pages are sent (select_sections), capped at llm.max_input_tokens_per_order.
* Cache key = sha256(pdf_sha256 | prompt_version | model) → llm_cache; re-runs cost nothing.
* Output forced via a single tool (input_schema = ExtractedOrder JSON schema), validated with pydantic;
  one retry feeding the validation error back, else needs_review.
* Evidence check: every snippet must occur verbatim (whitespace-normalised) on its cited page, and every
  printed amount must be parseable and present in the page text; failures → needs_review.
* Monthly ₹ spend cap enforced before every call. Estimates are shown before bulk runs.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError

from . import db
from .amounts import parse_inr
from .config import REPO_ROOT, Config
from .schema import PROMPT_VERSION, ExtractedOrder, json_schema

log = logging.getLogger(__name__)
CHARS_PER_TOKEN = 3.5            # conservative for English legal text
TOOL_NAME = "record_order"
KEYWORDS = re.compile(
    r"penalt|direct(?:ed|ion)|debar|restrain|disgorg|noticee|hereby|settle|without admitting|"
    r"regulation|section\s+\d+|appeal|securities appellate|SAT|set aside|interim|ex-parte|confirm|"
    r"exonerat|disposed|jointly and severally|ORDER", re.IGNORECASE)


def est_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


# ------------------------------------------------------------------ sections
def select_sections(pages: list[tuple[int, str]], max_tokens: int) -> list[tuple[int, str]]:
    """Pick pages in priority order until the token budget is hit: first 2 (header/parties/date),
    last 3 (operative order/signature), then pages by keyword density. Returned in page order."""
    if not pages:
        return []
    n = len(pages)
    prio = [0, 1] + [n - 1, n - 2, n - 3]
    density = sorted(range(2, max(2, n - 3)),
                     key=lambda i: -len(KEYWORDS.findall(pages[i][1])) / max(1, len(pages[i][1])))
    chosen: list[int] = []
    used = 0
    for i in prio + density:
        if i < 0 or i >= n or i in chosen:
            continue
        cost = est_tokens(pages[i][1]) + 6
        if used + cost > max_tokens:
            if i in prio and not chosen:       # always send at least something
                chosen.append(i)
                used += cost
            continue
        chosen.append(i)
        used += cost
    return [pages[i] for i in sorted(chosen)]


def render_sections(sections: list[tuple[int, str]]) -> str:
    return "\n\n".join(f"[page {no}]\n{text}" for no, text in sections)


# -------------------------------------------------------------------- prompt
def load_prompt() -> tuple[str, str]:
    raw = (REPO_ROOT / "prompts" / f"{PROMPT_VERSION}.md").read_text(encoding="utf-8")
    system = raw.split("System:", 1)[1].split("User:", 1)[0].strip()
    user_tpl = raw.split("User:", 1)[1].strip()
    return system, user_tpl


def build_user_prompt(user_tpl: str, meta: dict, sections_text: str) -> str:
    return (user_tpl.replace("{listing_title}", meta["listing_title"] or "")
            .replace("{source_category}", meta["source_category"] or "")
            .replace("{listing_date}", meta["listing_date"] or "")
            .replace("{sections}", sections_text)
            .replace("{json_schema}", "(enforced via the record_order tool schema)"))


def cache_key(pdf_sha256: str, prompt_version: str, model: str) -> str:
    return hashlib.sha256(f"{pdf_sha256}|{prompt_version}|{model}".encode()).hexdigest()


# ---------------------------------------------------------------------- cost
def price(cfg: Config, model: str) -> tuple[float, float]:
    p = cfg.llm["prices_usd_per_mtok"].get(model)
    if not p:
        raise KeyError(f"no price configured for {model} in config.yaml llm.prices_usd_per_mtok")
    return float(p["input"]), float(p["output"])


def cost_inr(cfg: Config, model: str, in_tok: int, out_tok: int, batch: bool = False) -> float:
    pi, po = price(cfg, model)
    usd = (in_tok * pi + out_tok * po) / 1e6
    if batch:
        usd *= float(cfg.llm.get("batch_discount", 0.5))
    return usd * float(cfg.llm["usd_inr"])


def month_spend(con) -> float:
    r = con.execute("SELECT cost_inr FROM llm_spend WHERE month=?", (datetime.now().strftime("%Y-%m"),)).fetchone()
    return float(r[0]) if r else 0.0


def _record_spend(con, in_tok: int, out_tok: int, inr: float) -> None:
    with db.tx(con):
        con.execute(
            "INSERT INTO llm_spend(month,input_tokens,output_tokens,cost_inr) VALUES(?,?,?,?) "
            "ON CONFLICT(month) DO UPDATE SET input_tokens=input_tokens+excluded.input_tokens, "
            "output_tokens=output_tokens+excluded.output_tokens, cost_inr=cost_inr+excluded.cost_inr",
            (datetime.now().strftime("%Y-%m"), in_tok, out_tok, inr))


# ------------------------------------------------------------------ estimate
def _pending(con, limit=None):
    q = ("SELECT order_id, listing_title, source_category, listing_date, pdf_sha256 FROM orders "
         "WHERE has_text_layer=1 AND fetch_status='text_extracted' AND extraction_status='pending' "
         "ORDER BY listing_date DESC")
    if limit:
        q += f" LIMIT {int(limit)}"
    return con.execute(q).fetchall()


def _pages(con, order_id) -> list[tuple[int, str]]:
    return [(r[0], r[1]) for r in con.execute(
        "SELECT page_no, text FROM order_pages WHERE order_id=? ORDER BY page_no", (order_id,))]


def estimate(cfg: Config, con, models: list[str], limit: int | None = None) -> dict:
    """Token + ₹ estimate for pending orders (no API calls; chars/3.5 heuristic)."""
    system, user_tpl = load_prompt()
    overhead = est_tokens(system) + est_tokens(user_tpl) + est_tokens(json.dumps(json_schema()))
    n, in_total = 0, 0
    for row in _pending(con, limit):
        if con.execute("SELECT 1 FROM llm_cache WHERE cache_key=?",
                       (cache_key(row["pdf_sha256"], PROMPT_VERSION, models[0]),)).fetchone():
            continue
        secs = select_sections(_pages(con, row["order_id"]), int(cfg.llm["max_input_tokens_per_order"]))
        in_total += overhead + sum(est_tokens(t) + 6 for _, t in secs)
        n += 1
    out_total = n * int(cfg.llm["est_output_tokens"])
    res = {"orders": n, "input_tokens": in_total, "output_tokens": out_total, "models": {}}
    cap = float(cfg.llm["monthly_spend_cap_inr"])
    for m in models:
        c, cb = cost_inr(cfg, m, in_total, out_total), cost_inr(cfg, m, in_total, out_total, batch=True)
        res["models"][m] = {"inr": round(c, 2), "inr_batch": round(cb, 2),
                            "months_of_cap": round(c / cap, 2) if cap else None}
    res["month_spend_inr"] = round(month_spend(con), 2)
    res["monthly_cap_inr"] = cap
    return res


def format_estimate(e: dict) -> str:
    lines = [f"{e['orders']} orders · ~{e['input_tokens']:,} input + ~{e['output_tokens']:,} output tokens",
             f"month spend so far ₹{e['month_spend_inr']} / cap ₹{e['monthly_cap_inr']}"]
    for m, v in e["models"].items():
        lines.append(f"  {m}: ₹{v['inr']:,}  (batch ₹{v['inr_batch']:,})  = {v['months_of_cap']}× monthly cap")
    return "\n".join(lines)


# -------------------------------------------------------------- verification
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def verify(order: ExtractedOrder, pages: dict[int, str]) -> list[str]:
    """Return problems: snippets not verbatim on cited page, unparseable / absent amounts."""
    norm = {k: _norm(v) for k, v in pages.items()}
    probs: list[str] = []

    def check(ev, label):
        t = norm.get(ev.page)
        if t is None or _norm(ev.snippet) not in t:
            probs.append(f"{label}: snippet not found verbatim on page {ev.page}")

    check(order.evidence, "order")
    for v in order.order_wide_violations:
        check(v.evidence, f"violation {v.regulation}")
    for n in order.noticees:
        check(n.evidence, f"noticee {n.name_as_printed}")
        for v in n.violations:
            check(v.evidence, f"violation {v.regulation}/{n.name_as_printed}")
        for p in n.penalties:
            check(p.evidence, f"penalty {n.name_as_printed}")
            if parse_inr(p.amount_as_printed) is None:
                probs.append(f"penalty {n.name_as_printed}: cannot parse '{p.amount_as_printed}'")
            elif _norm(p.amount_as_printed) not in norm.get(p.evidence.page, "") and \
                    _norm(p.amount_as_printed) not in " ".join(norm.values()):
                probs.append(f"penalty {n.name_as_printed}: amount not present in text")
    return probs


# ---------------------------------------------------------------------- call
@dataclass
class CallResult:
    order: ExtractedOrder | None
    raw: str
    in_tok: int
    out_tok: int
    error: str | None = None


class SpendCapReached(Exception):
    pass


def _call(client, cfg: Config, model: str, system: str, user: str, retry_note: str | None) -> tuple[str, int, int]:
    msgs = [{"role": "user", "content": user + (f"\n\nYour previous output was invalid: {retry_note}\n"
                                                "Return corrected JSON." if retry_note else "")}]
    resp = client.messages.create(
        model=model, max_tokens=int(cfg.llm["max_output_tokens"]), temperature=0, system=system, messages=msgs,
        tools=[{"name": TOOL_NAME, "description": "Record the structured facts extracted from the order.",
                "input_schema": json_schema()}],
        tool_choice={"type": "tool", "name": TOOL_NAME})
    block = next((b for b in resp.content if getattr(b, "type", "") == "tool_use"), None)
    raw = json.dumps(block.input) if block else ""
    return raw, resp.usage.input_tokens, resp.usage.output_tokens


def extract_order(cfg: Config, con, client, row, model: str) -> str:
    """Returns 'done' | 'needs_review' | 'failed' | 'cached'. Raises SpendCapReached."""
    key = cache_key(row["pdf_sha256"], PROMPT_VERSION, model)
    hit = con.execute("SELECT response_json, valid FROM llm_cache WHERE cache_key=?", (key,)).fetchone()
    pages = _pages(con, row["order_id"])
    page_text = dict(pages)
    if hit:
        raw, valid = hit["response_json"], bool(hit["valid"])
    else:
        system, user_tpl = load_prompt()
        secs = select_sections(pages, int(cfg.llm["max_input_tokens_per_order"]))
        user = build_user_prompt(user_tpl, dict(row), render_sections(secs))
        cap = float(cfg.llm["monthly_spend_cap_inr"])
        tot_in = tot_out = 0
        raw, valid, err = "", False, None
        for attempt in range(1 + int(cfg.llm["retry_on_invalid_json"])):
            if month_spend(con) >= cap:
                raise SpendCapReached(f"monthly cap ₹{cap} reached")
            raw, i, o = _call(client, cfg, model, system, user, err)
            tot_in, tot_out = tot_in + i, tot_out + o
            _record_spend(con, i, o, cost_inr(cfg, model, i, o))
            try:
                ExtractedOrder.model_validate_json(raw)
                valid = True
                break
            except (ValidationError, ValueError) as e:
                err = str(e)[:800]
        with db.tx(con):
            con.execute("INSERT OR REPLACE INTO llm_cache(cache_key,order_id,model,prompt_version,input_tokens,"
                        "output_tokens,cost_inr,response_json,valid) VALUES(?,?,?,?,?,?,?,?,?)",
                        (key, row["order_id"], model, PROMPT_VERSION, tot_in, tot_out,
                         cost_inr(cfg, model, tot_in, tot_out), raw, int(valid)))
    if not valid:
        _mark(con, row["order_id"], "needs_review", model, "invalid JSON after retry")
        return "needs_review"
    order = ExtractedOrder.model_validate_json(raw)
    probs = verify(order, page_text)
    if order.needs_review or probs:
        _mark(con, row["order_id"], "needs_review", model,
              "; ".join(filter(None, [order.review_notes, *probs]))[:900])
        return "needs_review"
    from . import store
    store.apply_order(con, row["order_id"], order, model, PROMPT_VERSION)   # sets status=done
    return "done"


def _mark(con, order_id, status, model, notes):
    with db.tx(con):
        con.execute("UPDATE orders SET extraction_status=?, extraction_model=?, prompt_version=?, "
                    "extraction_notes=COALESCE(?, extraction_notes), updated_at=datetime('now') WHERE order_id=?",
                    (status, model, PROMPT_VERSION, notes, order_id))


def run(cfg: Config, con, client, model: str | None = None, limit: int | None = None) -> dict:
    model = model or cfg.llm["model"]
    stats = {"done": 0, "needs_review": 0, "failed": 0, "stopped": None}
    for row in _pending(con, limit):
        try:
            stats[extract_order(cfg, con, client, row, model)] += 1
        except SpendCapReached as e:
            stats["stopped"] = str(e)
            break
        except Exception as e:      # network/API errors: log, leave pending, continue
            log.error("extract %s: %s", row["order_id"], e)
            stats["failed"] += 1
    return stats
