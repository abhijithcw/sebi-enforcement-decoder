"""Parse Indian-notation rupee amounts to integer rupees.

Handles: "Rs. 5,00,000/-", "₹10,00,000", "Rs. 10 lakh", "Rupees Ten Lakh only" (digits required),
"Rs 1.5 crore", "INR 2,50,000", "Rs.25 lacs". Returns None when nothing parseable.
"""
from __future__ import annotations

import re

_UNITS = {
    "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000,
    "crore": 10_000_000, "crores": 10_000_000, "cr": 10_000_000,
    "thousand": 1_000, "k": 1_000,
}
_NUM = r"(?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_UNIT = r"(?:\s*(?P<unit>lakhs?|lacs?|crores?|cr|thousand)\b)?"
_RE = re.compile(
    r"(?:rs\.?|₹|inr|rupees)\s*" + _NUM + _UNIT + r"\s*(?:/-)?",
    re.IGNORECASE,
)


def parse_inr(text: str) -> int | None:
    m = _RE.search(text)
    if not m:
        return None
    num = float(m.group("num").replace(",", ""))
    unit = (m.group("unit") or "").lower()
    mult = _UNITS.get(unit, 1)
    return int(round(num * mult))


def format_inr(amount: int) -> str:
    """12345678 -> '₹1,23,45,678'."""
    s = str(abs(amount))
    if len(s) <= 3:
        body = s
    else:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        body = ",".join(parts) + "," + tail
    return ("-" if amount < 0 else "") + "₹" + body
