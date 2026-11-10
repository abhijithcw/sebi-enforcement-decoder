"""Entity resolution helpers: name normalisation, canonical keys, PAN masking.

Canonical key precedence (most reliable first):
  cin:<CIN>  >  isin:<ISIN>  >  pan:<PAN>  >  name:<normalised name>
A `name:` key is a provisional identity; later runs may merge it into a cin:/isin:/pan: key
once a stronger identifier is seen (recorded in entity_aliases).
"""
from __future__ import annotations

import re

_SUFFIXES = [
    r"private", r"pvt", r"limited", r"ltd", r"llp", r"plc", r"inc", r"co",
    r"company", r"corporation", r"corp", r"and", r"&",
]
_SUFFIX_RE = re.compile(r"\b(?:" + "|".join(_SUFFIXES) + r")\b\.?", re.IGNORECASE)
_HONORIFIC_RE = re.compile(r"^(?:m/s\.?|mr\.?|mrs\.?|ms\.?|shri|smt\.?|sri|dr\.?)\s+", re.IGNORECASE)
_NOTICEE_RE = re.compile(r"\(?\bnoticee\s*(?:no\.?)?\s*\d+\)?", re.IGNORECASE)
_INITIALS_RE = re.compile(r"\b(?:[A-Za-z]\.\s?){2,}")   # "A.B.C. " / "A. B. C." -> "ABC"
_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")

PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
CIN_RE = re.compile(r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$")
ISIN_RE = re.compile(r"^INE[0-9A-Z]{9}$")


def normalise(name: str) -> str:
    """'M/s. Zee Entertainment Enterprises Ltd. (Noticee No. 2)' -> 'ZEE ENTERTAINMENT ENTERPRISES'."""
    s = name.strip()
    s = _NOTICEE_RE.sub(" ", s)
    s = _HONORIFIC_RE.sub("", s)
    s = _INITIALS_RE.sub(lambda m: m.group(0).replace(".", "").replace(" ", "") + " ", s)
    s = _PUNCT_RE.sub(" ", s)
    s = _SUFFIX_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip().upper()
    return s


def entity_id(*, cin: str | None = None, isin: str | None = None,
              pan: str | None = None, name: str | None = None) -> str:
    if cin and CIN_RE.match(cin):
        return f"cin:{cin}"
    if isin and ISIN_RE.match(isin):
        return f"isin:{isin}"
    if pan and PAN_RE.match(pan):
        return f"pan:{pan}"
    if name:
        return f"name:{normalise(name)}"
    raise ValueError("entity needs at least a name")


def mask_pan(pan: str | None) -> str | None:
    """Display rule: first 3 + last 1 char visible. 'ABCDE1234F' -> 'ABC******F'."""
    if not pan:
        return None
    pan = pan.strip().upper()
    if len(pan) < 5:
        return "*" * len(pan)
    return pan[:3] + "*" * (len(pan) - 4) + pan[-1]


def guess_kind(name: str) -> str:
    """'company' if the raw name carries a corporate suffix, else 'individual'."""
    return "company" if _SUFFIX_RE.search(name) or name.lower().startswith("m/s") else "individual"
