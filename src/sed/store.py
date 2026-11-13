"""Step 5 — map a validated ExtractedOrder into SQLite (orders, entities, noticees, penalties,
violations, appeals). Idempotent per order: re-applying replaces that order's noticees/penalties/
violations; entities are upserted on their canonical key.

Entity resolution (see sed.entity): cin > isin > pan > normalised name. A name-only entity is
promoted/merged when a stronger id for the same normalised name shows up later. No fuzzy merging
happens silently — fuzzy matching is a search-time feature, not an identity decision.
Amounts are parsed from the printed string here (never by the model).
"""
from __future__ import annotations

import logging

from . import db, entity
from .amounts import parse_inr
from .schema import ExtractedOrder, ExtractedViolation, Outcome

log = logging.getLogger(__name__)

_APPEAL_STATUS = {"none": "none_known", "sat_pending": "appealed_to_sat", "sat_set_aside": "sat_set_aside",
                  "sat_modified": "sat_modified", "sat_upheld": "sat_upheld"}
_APPEAL_RESULT = {"sat_pending": "pending", "sat_set_aside": "set_aside", "sat_modified": "modified",
                  "sat_upheld": "upheld"}

_REG_CODES = [
    ("insider trading", "PIT"), ("fraudulent and unfair", "PFUTP"), ("substantial acquisition", "SAST"),
    ("listing obligations", "LODR"), ("issue of capital", "ICDR"), ("sebi act", "SEBI_ACT"),
    ("securities contracts", "SCRA"), ("mutual fund", "MF"), ("stock brokers", "BROKERS"),
    ("intermediaries", "INTERMEDIARIES"), ("research analyst", "RA"), ("investment advis", "IA"),
    ("depositories", "DEPOSITORIES"), ("portfolio managers", "PMS"), ("alternative investment", "AIF"),
    ("buy-back", "BUYBACK"), ("takeover", "SAST"), ("collective investment", "CIS"),
]


def reg_code(regulation: str) -> str:
    low = regulation.lower()
    for key, code in _REG_CODES:
        if key in low:
            return code
    return "OTHER"


# ------------------------------------------------------------------ entities
def _merge_entity(con, old: str, new: str) -> None:
    """Move everything from `old` into `new` and drop `old` (aliases keep the old spelling)."""
    con.execute("UPDATE noticees SET entity_id=? WHERE entity_id=?", (new, old))
    for r in con.execute("SELECT alias_norm, alias_raw, seen_count FROM entity_aliases WHERE entity_id=?",
                         (old,)).fetchall():
        con.execute("INSERT INTO entity_aliases(alias_norm,entity_id,alias_raw,seen_count) VALUES(?,?,?,?) "
                    "ON CONFLICT(alias_norm,entity_id) DO UPDATE SET seen_count=seen_count+excluded.seen_count",
                    (r[0], new, r[1], r[2]))
    con.execute("DELETE FROM entities WHERE entity_id=?", (old,))


def resolve_entity(con, name: str, kind: str, *, pan=None, cin=None, isin=None, din=None) -> str:
    norm = entity.normalise(name)
    eid = entity.entity_id(cin=cin, isin=isin, pan=pan, name=name)
    strong = not eid.startswith("name:")
    method = "cin_printed" if eid.startswith("cin:") else "isin_printed" if eid.startswith("isin:") \
        else "pan_printed" if eid.startswith("pan:") else "name_exact"
    name_eid = f"name:{norm}"
    if strong and con.execute("SELECT 1 FROM entities WHERE entity_id=?", (name_eid,)).fetchone():
        # a provisional name-only entity exists for this spelling: promote it
        if not con.execute("SELECT 1 FROM entities WHERE entity_id=?", (eid,)).fetchone():
            con.execute("INSERT INTO entities(entity_id,canonical_name,normalized_name,entity_kind,pan,cin,isin,din,"
                        "resolution_method,resolution_conf) SELECT ?,canonical_name,normalized_name,entity_kind,?,?,?,?,?,1.0 "
                        "FROM entities WHERE entity_id=?", (eid, pan, cin, isin, din, method, name_eid))
        _merge_entity(con, name_eid, eid)
    elif not strong:
        # reuse a strong entity that already carries this normalised name (same spelling, id seen earlier)
        hit = con.execute("SELECT entity_id FROM entities WHERE normalized_name=? AND entity_id NOT LIKE 'name:%' "
                          "AND entity_kind=? ORDER BY resolution_conf DESC LIMIT 2", (norm, kind)).fetchall()
        if len(hit) == 1:
            eid, method = hit[0][0], "name_exact"
    con.execute(
        "INSERT INTO entities(entity_id,canonical_name,normalized_name,entity_kind,pan,cin,isin,din,"
        "resolution_method,resolution_conf) VALUES(?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(entity_id) DO UPDATE SET pan=COALESCE(entities.pan,excluded.pan), "
        "cin=COALESCE(entities.cin,excluded.cin), isin=COALESCE(entities.isin,excluded.isin), "
        "din=COALESCE(entities.din,excluded.din), updated_at=datetime('now')",
        (eid, name.strip(), norm, kind, pan, cin, isin, din, method, 1.0 if strong else 0.6))
    con.execute("INSERT OR IGNORE INTO entity_aliases(alias_norm,entity_id,alias_raw) VALUES(?,?,?)",
                (norm, eid, name.strip()))
    return eid


# --------------------------------------------------------------------- apply
def _insert_violation(con, order_id, noticee_id, v: ExtractedViolation):
    con.execute(
        "INSERT OR IGNORE INTO violations(order_id,noticee_id,regulation,clause,regulation_code,finding,page_ref,snippet)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (order_id, noticee_id, v.regulation, v.clause, reg_code(v.regulation), v.finding.value,
         v.evidence.page, v.evidence.snippet))


def apply_order(con, order_id: str, o: ExtractedOrder, model: str, prompt_version: str) -> dict:
    stats = {"noticees": 0, "penalties": 0, "violations": 0}
    subj_norm = entity.normalise(o.subject_company) if o.subject_company else None
    with db.tx(con):
        con.execute("DELETE FROM noticees WHERE order_id=?", (order_id,))       # cascades penalties/violations
        con.execute("DELETE FROM violations WHERE order_id=?", (order_id,))
        con.execute("DELETE FROM appeals WHERE order_id=? AND source='sebi_order'", (order_id,))
        con.execute(
            "UPDATE orders SET sebi_ref=COALESCE(?,sebi_ref), order_date=COALESCE(?,order_date), "
            "issuing_authority=COALESCE(?,issuing_authority), signatory=COALESCE(?,signatory), matter_summary=?, order_type=?, "
            "is_ex_parte=?, appeal_status=?, extraction_model=?, prompt_version=?, extraction_status='done', "
            "updated_at=datetime('now') WHERE order_id=?",
            (o.sebi_ref, o.order_date.isoformat() if o.order_date else None, o.issuing_authority, o.signatory,
             o.matter_summary, o.order_type.value, int(o.is_ex_parte), _APPEAL_STATUS[o.appeal_mentioned], model, prompt_version,
             order_id))
        if o.appeal_mentioned != "none":
            con.execute("INSERT OR IGNORE INTO appeals(order_id,forum,appeal_no,result,source) VALUES(?,?,?,?,?)",
                        (order_id, "SAT", "", _APPEAL_RESULT[o.appeal_mentioned], "sebi_order"))
        for v in o.order_wide_violations:
            _insert_violation(con, order_id, None, v)
            stats["violations"] += 1
        for n in o.noticees:
            kind = "company" if n.role.value in ("company", "broker") and entity.guess_kind(n.name_as_printed) == "company" \
                else entity.guess_kind(n.name_as_printed)
            isin = o.subject_isin if subj_norm and entity.normalise(n.name_as_printed) == subj_norm else None
            eid = resolve_entity(con, n.name_as_printed, kind, pan=n.pan, cin=n.cin, isin=isin, din=n.din)
            cur = con.execute(
                "INSERT INTO noticees(order_id,noticee_no,name_as_printed,entity_id,role,role_detail,outcome,"
                "outcome_detail,debarment_months,page_ref,snippet,confidence) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(order_id,name_as_printed) DO UPDATE SET outcome=excluded.outcome",
                (order_id, n.noticee_no, n.name_as_printed, eid, n.role.value, n.role_detail, n.outcome.value,
                 n.outcome_detail, n.debarment_months, n.evidence.page, n.evidence.snippet, n.confidence))
            nid = cur.lastrowid or con.execute("SELECT noticee_id FROM noticees WHERE order_id=? AND name_as_printed=?",
                                               (order_id, n.name_as_printed)).fetchone()[0]
            stats["noticees"] += 1
            for p in n.penalties:
                amt = parse_inr(p.amount_as_printed)
                if amt is None:
                    continue                     # extract.verify already flags these for review
                con.execute(
                    "INSERT OR IGNORE INTO penalties(noticee_id,order_id,amount_inr,amount_as_printed,kind,"
                    "joint_and_several,joint_group,statutory_provision,page_ref,snippet) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (nid, order_id, amt, p.amount_as_printed, p.kind.value, int(p.joint_and_several), p.joint_group,
                     p.statutory_provision, p.evidence.page, p.evidence.snippet))
                stats["penalties"] += 1
            for v in n.violations:
                _insert_violation(con, order_id, nid, v)
                stats["violations"] += 1
    return stats


def rebuild_from_cache(con, model: str | None = None) -> dict:
    """Re-apply every valid cached extraction (e.g. after changing resolution rules). No API calls."""
    from .extract import cache_key
    from .schema import PROMPT_VERSION
    stats = {"orders": 0}
    q = "SELECT order_id, pdf_sha256, extraction_model FROM orders WHERE extraction_status='done'"
    for r in con.execute(q).fetchall():
        m = model or r["extraction_model"]
        hit = con.execute("SELECT response_json FROM llm_cache WHERE cache_key=? AND valid=1",
                          (cache_key(r["pdf_sha256"], PROMPT_VERSION, m),)).fetchone()
        if hit:
            apply_order(con, r["order_id"], ExtractedOrder.model_validate_json(hit[0]), m, PROMPT_VERSION)
            stats["orders"] += 1
    return stats


__all__ = ["apply_order", "resolve_entity", "rebuild_from_cache", "reg_code", "Outcome"]
