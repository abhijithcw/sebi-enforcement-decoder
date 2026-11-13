import sqlite3
from types import SimpleNamespace as NS

import pytest

from sed import config, db, extract

PAGE1 = "ORDER\nIn the matter of Foo Ltd\nNoticee 1: Ramesh Kumar, Promoter of Foo Ltd.\nDate: March 3, 2024"
PAGE2 = "A penalty of Rs. 5,00,000/- is imposed on Ramesh Kumar under Section 15HA of the SEBI Act, 1992."


def good_payload(snippet2=PAGE2[:60]):
    ev1 = {"page": 1, "snippet": "Noticee 1: Ramesh Kumar, Promoter of Foo Ltd."}
    return {
        "order_type": "adjudication", "order_date": "2024-03-03",
        "matter_summary": "Adjudication concerning alleged non-disclosure by a promoter.",
        "noticees": [{"name_as_printed": "Ramesh Kumar", "role": "promoter", "outcome": "penalty",
                      "penalties": [{"amount_as_printed": "Rs. 5,00,000/-", "evidence": {"page": 2, "snippet": snippet2}}],
                      "evidence": ev1, "confidence": 0.9}],
        "evidence": ev1,
    }


class FakeClient:
    def __init__(self, *payloads):
        self.payloads, self.calls = list(payloads), 0
        self.messages = self

    def create(self, **kw):
        self.calls += 1
        p = self.payloads.pop(0)
        return NS(content=[NS(type="tool_use", input=p)], usage=NS(input_tokens=3000, output_tokens=800))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SED_DATA_ROOT", str(tmp_path / "d"))
    config.load.cache_clear()
    cfg = config.load()
    con = sqlite3.connect(tmp_path / "t.sqlite"); con.row_factory = sqlite3.Row
    db.init_db(con)
    con.execute("INSERT INTO orders(order_id,source_category,listing_title,html_url,pdf_sha256,has_text_layer,"
                "fetch_status,listing_date) VALUES('o1','adjudicating_officer','T','u','sha1',1,'text_extracted','2024-03-04')")
    for i, t in enumerate([PAGE1, PAGE2], 1):
        con.execute("INSERT INTO order_pages VALUES('o1',?,?,?)", (i, t, len(t)))
    con.commit()
    yield cfg, con
    config.load.cache_clear()


def row(con):
    return con.execute("SELECT * FROM orders WHERE order_id='o1'").fetchone()


def test_happy_path_cache_and_spend(env):
    cfg, con = env
    cl = FakeClient(good_payload())
    assert extract.run(cfg, con, cl, "claude-sonnet-5-5")["done"] == 1
    o = row(con)
    assert o["extraction_status"] == "done" and o["extraction_model"] == "claude-sonnet-5-5"
    assert o["prompt_version"] == "extract_v1"
    assert extract.month_spend(con) == pytest.approx((3000 * 2 + 800 * 10) / 1e6 * 84.0)
    # cached by (sha, prompt, model): a reset order costs no second call
    con.execute("UPDATE orders SET extraction_status='pending'"); con.commit()
    assert extract.run(cfg, con, cl, "claude-sonnet-5-5")["done"] == 1 and cl.calls == 1
    # different model = different cache key
    con.execute("UPDATE orders SET extraction_status='pending'"); con.commit()
    extract.run(cfg, con, FakeClient(good_payload()), "claude-haiku-5-5")
    assert con.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0] == 2


def test_invalid_then_retry_then_review(env):
    cfg, con = env
    bad = {"order_type": "nonsense"}
    cl = FakeClient(bad, good_payload())
    assert extract.run(cfg, con, cl)["done"] == 1 and cl.calls == 2
    con.execute("DELETE FROM llm_cache"); con.execute("UPDATE orders SET extraction_status='pending'"); con.commit()
    cl = FakeClient(bad, bad)
    assert extract.run(cfg, con, cl)["needs_review"] == 1
    assert row(con)["extraction_status"] == "needs_review"


def test_fabricated_snippet_flagged(env):
    cfg, con = env
    assert extract.run(cfg, con, FakeClient(good_payload("a penalty of Rs. 9,99,999/- was levied here")))[
        "needs_review"] == 1
    assert "not found verbatim" in row(con)["extraction_notes"]


def test_spend_cap_stops(env):
    cfg, con = env
    cfg.llm["monthly_spend_cap_inr"] = 0.0001
    extract._record_spend(con, 1, 1, 1.0)
    r = extract.run(cfg, con, FakeClient(good_payload()))
    assert r["stopped"] and row(con)["extraction_status"] == "pending"


def test_estimate_and_sections(env):
    cfg, con = env
    e = extract.estimate(cfg, con, ["claude-sonnet-5-5", "claude-haiku-5-5"])
    assert e["orders"] == 1 and e["input_tokens"] > 0
    s, h = e["models"]["claude-sonnet-5-5"]["inr"], e["models"]["claude-haiku-5-5"]["inr"]
    assert s > h * 15
    assert "claude-haiku-5-5" in extract.format_estimate(e)
    pages = [(i, ("penalty noticee " if i == 7 else "filler ") * 300) for i in range(1, 41)]
    sel = extract.select_sections(pages, 6000)
    nums = [p for p, _ in sel]
    assert 1 in nums and 2 in nums and 40 in nums and 7 in nums
    assert sum(extract.est_tokens(t) for _, t in sel) <= 6000 + 200


def test_temperature_rejected_falls_back(env):
    cfg, con = env

    class Picky(FakeClient):
        def create(self, **kw):
            if "temperature" in kw:
                raise ValueError("temperature is not supported for this model")
            return super().create(**kw)
    assert extract.run(cfg, con, Picky(good_payload()), "claude-sonnet-5-5")["done"] == 1
