import pytest

from sed.amounts import format_inr, parse_inr


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Rs. 5,00,000/- (Rupees Five Lakh only)", 500_000),
        ("₹10,00,000", 1_000_000),
        ("Rs. 10 lakh", 1_000_000),
        ("Rs 1.5 crore", 15_000_000),
        ("INR 2,50,000", 250_000),
        ("Rs.25 lacs", 2_500_000),
        ("penalty of Rs. 12,34,56,789/- is imposed", 123_456_789),
        ("Rupees 1,000", 1_000),
        ("no amount here", None),
    ],
)
def test_parse_inr(text, expected):
    assert parse_inr(text) == expected


def test_format_inr():
    assert format_inr(500_000) == "₹5,00,000"
    assert format_inr(12_345_678) == "₹1,23,45,678"
    assert format_inr(999) == "₹999"
    assert format_inr(1_000) == "₹1,000"
