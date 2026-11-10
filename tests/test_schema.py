import pytest
from pydantic import ValidationError

from sed.schema import ExtractedOrder, json_schema

EV = {"page": 3, "snippet": "a penalty of Rs. 5,00,000/- is imposed on the Noticee"}


def _order(**over):
    base = {
        "order_type": "adjudication",
        "matter_summary": "Adjudication proceedings concerning alleged non-disclosure.",
        "noticees": [
            {
                "name_as_printed": "Ramesh Kumar",
                "role": "promoter",
                "outcome": "penalty",
                "penalties": [{"amount_as_printed": "Rs. 5,00,000/-", "evidence": EV}],
                "evidence": EV,
                "confidence": 0.9,
            }
        ],
        "evidence": EV,
    }
    base.update(over)
    return base


def test_valid_order():
    o = ExtractedOrder.model_validate(_order())
    assert o.noticees[0].penalties[0].amount_as_printed == "Rs. 5,00,000/-"
    assert o.needs_review is False


def test_evidence_required():
    bad = _order()
    del bad["noticees"][0]["evidence"]
    with pytest.raises(ValidationError):
        ExtractedOrder.model_validate(bad)


def test_penalty_without_amount_flags_review():
    o = ExtractedOrder.model_validate(_order(noticees=[{
        "name_as_printed": "Ramesh Kumar", "role": "promoter", "outcome": "penalty",
        "evidence": EV, "confidence": 0.5,
    }]))
    assert o.needs_review and "without amount" in o.review_notes


def test_pan_pattern():
    with pytest.raises(ValidationError):
        ExtractedOrder.model_validate(_order(noticees=[{
            "name_as_printed": "X", "role": "other", "outcome": "unknown", "pan": "BAD",
            "evidence": EV, "confidence": 0.5,
        }]))


def test_settlement_sets_flag():
    o = ExtractedOrder.model_validate(_order(order_type="settlement"))
    assert o.is_settlement is True


def test_json_schema_has_enums():
    s = json_schema()
    assert "ExtractedNoticee" in s["$defs"]
