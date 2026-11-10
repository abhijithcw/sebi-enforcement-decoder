"""Extraction schema — the JSON the LLM must return for one order, validated with pydantic.

Design rules:
- Every extracted value carries Evidence(page, snippet). No evidence -> validation error -> retry
  once -> needs_review. The model never invents figures.
- Amounts are returned AS PRINTED (string); Python parses them (sed.amounts.parse_inr).
  This keeps arithmetic out of the model.
- Enums match the CHECK constraints in sql/schema.sql exactly.
"""
from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

PROMPT_VERSION = "extract_v1"


class Evidence(BaseModel):
    page: int = Field(ge=1, description="1-based page number in the PDF")
    snippet: str = Field(min_length=8, max_length=600, description="verbatim text supporting the value")


class OrderType(StrEnum):
    interim = "interim"              # interim + ex-parte ad-interim
    confirmatory = "confirmatory"
    final = "final"
    settlement = "settlement"
    adjudication = "adjudication"
    revocation = "revocation"
    other = "other"


class Role(StrEnum):
    company = "company"
    promoter = "promoter"
    director = "director"
    kmp = "kmp"
    broker = "broker"
    other = "other"


class Outcome(StrEnum):
    penalty = "penalty"
    directions = "directions"        # debarment / restraint / cease-and-desist
    disgorgement = "disgorgement"
    warning = "warning"
    disposed = "disposed"            # exonerated / dropped
    settled = "settled"
    unknown = "unknown"


class PenaltyKind(StrEnum):
    penalty = "penalty"
    disgorgement = "disgorgement"
    settlement_amount = "settlement_amount"
    interest = "interest"


class Finding(StrEnum):
    alleged = "alleged"
    established = "established"
    not_established = "not_established"
    settled = "settled"


class ExtractedPenalty(BaseModel):
    amount_as_printed: str = Field(description='verbatim, e.g. "Rs. 5,00,000/- (Rupees Five Lakh only)"')
    kind: PenaltyKind = PenaltyKind.penalty
    joint_and_several: bool = False
    joint_group: str | None = Field(default=None, description="label shared by noticees liable jointly, e.g. 'A'")
    statutory_provision: str | None = Field(default=None, description='e.g. "Section 15HA of SEBI Act, 1992"')
    evidence: Evidence


class ExtractedViolation(BaseModel):
    regulation: str = Field(description='full name as printed, e.g. "SEBI (Prohibition of Insider Trading) Regulations, 2015"')
    clause: str | None = Field(default=None, description='e.g. "Regulation 4(1)" or "Section 12A(d)"')
    finding: Finding = Finding.alleged
    evidence: Evidence


class ExtractedNoticee(BaseModel):
    noticee_no: int | None = None
    name_as_printed: str = Field(min_length=2)
    role: Role
    role_detail: str | None = None
    pan: str | None = Field(default=None, pattern=r"^[A-Z]{5}[0-9]{4}[A-Z]$", description="only if printed")
    cin: str | None = Field(default=None, pattern=r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$")
    din: str | None = Field(default=None, pattern=r"^\d{8}$")
    outcome: Outcome
    outcome_detail: str | None = None
    debarment_months: int | None = Field(default=None, ge=0)
    penalties: list[ExtractedPenalty] = []
    violations: list[ExtractedViolation] = Field(default=[], description="violations attributed to this noticee")
    evidence: Evidence = Field(description="supports role + outcome")
    confidence: float = Field(ge=0, le=1)

    @field_validator("pan", "cin", "din", mode="before")
    @classmethod
    def _upper(cls, v):
        return v.strip().upper() if isinstance(v, str) and v.strip() else None


class ExtractedOrder(BaseModel):
    sebi_ref: str | None = Field(default=None, description='order/reference number as printed')
    order_date: date | None = None
    issuing_authority: str | None = Field(default=None, description='"Whole Time Member", "Adjudicating Officer", ...')
    signatory: str | None = None
    order_type: OrderType
    is_ex_parte: bool = False
    is_settlement: bool = False
    subject_company: str | None = Field(default=None, description="scrip / company the matter concerns, if any")
    subject_isin: str | None = Field(default=None, pattern=r"^INE[0-9A-Z]{9}$")
    matter_summary: str = Field(max_length=600, description="2-3 neutral sentences; no adjectives like 'violator'")
    noticees: list[ExtractedNoticee] = Field(min_length=1)
    order_wide_violations: list[ExtractedViolation] = []
    appeal_mentioned: Literal["none", "sat_pending", "sat_set_aside", "sat_modified", "sat_upheld"] = "none"
    evidence: Evidence = Field(description="supports order_type + date")
    needs_review: bool = Field(default=False, description="set when any field is ambiguous")
    review_notes: str | None = None

    @model_validator(mode="after")
    def _consistency(self):
        if self.order_type == OrderType.settlement and not self.is_settlement:
            self.is_settlement = True
        if self.is_ex_parte and self.order_type not in (OrderType.interim, OrderType.other):
            self.needs_review = True
            self.review_notes = (self.review_notes or "") + " ex_parte set on non-interim order;"
        for n in self.noticees:
            if n.outcome == Outcome.penalty and not n.penalties:
                self.needs_review = True
                self.review_notes = (self.review_notes or "") + f" {n.name_as_printed}: penalty outcome without amount;"
        return self


def json_schema() -> dict:
    return ExtractedOrder.model_json_schema()
