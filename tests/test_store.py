import sqlite3

import pytest

from sed import db, store
from sed.schema import ExtractedOrder

EV = {"page": 1, "snippet": "Noticee 1: Ramesh Kumar, Promoter of Foo Ltd."}


def order(**kw):
    d = {"order_type": "adjudication", "order_date": "2024-03-03", "sebi_ref": "Order/XY/2024-25/1",
         "matter_summary": "Neutral summary of the matter.", "subject_company": "Foo Ltd",
         "subject_isin": "INE123A01016", "evidence": EV, "appeal_mentioned": "sat_set_aside",
         "order_wide_violations": [{"regulation": "SEBI (PIT) Regulations, 2015 — Insider Trading", "clause": "Reg 4(1)",
                                    "finding": "established", "evidence": EV}],
         "noticees": [
             {"name_as_printed": "Foo Ltd.", "role": "company", "outcome": "penalty", "evidence": EV, "confidence": .9,
              "penalties": [{"amount_as_printed": "Rs. 10 lakh", "joint_and_several": True, "joint_group": "A",
                             "statutory_provision": "Section 15HA", "evidence": EV}]},
             {"name_as_printed": "Ramesh Kumar", "role": "promoter", "outcome": "penalty", "pan": "ABCDE1234F",
              "evidence": EV, "confidence": .9,
              "penalties": [{"amount_as_printed": "Rs. 10,00,000/-", "joint_and_several": True, "joint_group": "A",
                             "statutory_provision": "Section 15HA", "evidence": EV}],
              "violations": [{"regulation": "SEBI (PFUTP) Regulations, 2003 - Fraudulent and Unfair", "evidence": EV}]},
         ]}
    d.update(kw)
    return ExtractedOrder.model_validate(d)


@pytest.fixture
def con(tmp_path):
    c = sqlite3.connect(tmp_path / "t.sqlite"); c.row_factory = sqlite3.Row
    db.init_db(c)
    for i in ("o1", "o2"):
        c.execute("INSERT INTO orders(order_id,source_category,listing_title,html_url) VALUES(?,?,?,?)",
                  (i, "adjudicating_officer", "t", "u" + i))
    c.commit()
    return c


def test_apply_maps_everything(con):
    st = store.apply_order(con, "o1", order(), "m", "extract_v1")
    assert st == {"noticees": 2, "penalties": 2, "violations": 2}
    o = con.execute("SELECT * FROM orders WHERE order_id='o1'").fetchone()
    assert o["order_type"] == "adjudication" and o["appeal_status"] == "sat_set_aside" and o["order_date"] == "2024-03-03"
    assert [r[0] for r in con.execute("SELECT amount_inr FROM penalties")] == [1_000_000, 1_000_000]
    assert con.execute("SELECT COUNT(*) FROM penalties WHERE joint_and_several=1 AND joint_group='A'").fetchone()[0] == 2
    assert {r[0] for r in con.execute("SELECT regulation_code FROM violations")} == {"PIT", "PFUTP"}
    assert con.execute("SELECT result FROM appeals").fetchone()[0] == "set_aside"
    ids = {r[0] for r in con.execute("SELECT entity_id FROM entities")}
    assert ids == {"isin:INE123A01016", "pan:ABCDE1234F"}          # company via subject ISIN, person via PAN


def test_idempotent(con):
    for _ in range(2):
        store.apply_order(con, "o1", order(), "m", "extract_v1")
    assert con.execute("SELECT COUNT(*) FROM noticees").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM penalties").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM violations").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM appeals").fetchone()[0] == 1


def test_individuals_never_merged_by_name(con):
    no_pan = order(subject_isin=None, appeal_mentioned="none")
    no_pan.noticees[1].pan = None
    store.apply_order(con, "o1", no_pan, "m", "extract_v1")
    store.apply_order(con, "o2", order(), "m", "extract_v1")       # a Ramesh Kumar WITH a PAN
    ids = {r[0] for r in con.execute("SELECT entity_id FROM noticees WHERE name_as_printed='Ramesh Kumar'")}
    assert ids == {"name:RAMESH KUMAR", "pan:ABCDE1234F"}          # kept apart: could be different people


def test_company_promoted_when_isin_appears(con):
    first = order(subject_isin=None, appeal_mentioned="none")
    store.apply_order(con, "o1", first, "m", "extract_v1")
    assert con.execute("SELECT entity_id FROM noticees WHERE name_as_printed='Foo Ltd.'").fetchone()[0] == "name:FOO"
    store.apply_order(con, "o2", order(), "m", "extract_v1")       # subject ISIN now known
    assert {r[0] for r in con.execute("SELECT entity_id FROM noticees WHERE name_as_printed='Foo Ltd.'")} == {"isin:INE123A01016"}
    assert con.execute("SELECT COUNT(*) FROM entities WHERE entity_id='name:FOO'").fetchone()[0] == 0


def test_duplicate_noticee_names_keep_own_penalties(con):
    o = order(appeal_mentioned="none")
    dup = o.noticees[1].model_copy(deep=True)
    dup.penalties[0].amount_as_printed = "Rs. 2,00,000/-"
    o.noticees.append(dup)
    store.apply_order(con, "o1", o, "m", "extract_v1")
    assert con.execute("SELECT COUNT(*) FROM penalties").fetchone()[0] == 3


def test_reg_code():
    assert store.reg_code("SEBI (Listing Obligations and Disclosure Requirements) Regulations, 2015") == "LODR"
    assert store.reg_code("Companies Act") == "OTHER"
