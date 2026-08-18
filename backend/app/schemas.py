"""Pydantic request/response models."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ReviewRequest(BaseModel):
    decision: Literal["accept", "override", "reject"]
    final_max: int | None = Field(default=None, ge=0)
    final_rop: int | None = Field(default=None, ge=0)
    final_min: int | None = Field(default=None, ge=0)
    comment: str = ""
    justification: str = ""

    @model_validator(mode="after")
    def override_needs_values(self):
        if self.decision == "override":
            vals = (self.final_max, self.final_rop, self.final_min)
            if any(v is None for v in vals):
                raise ValueError("override requires final_max, final_rop and final_min")
            if not (self.final_max >= self.final_rop >= self.final_min):
                raise ValueError("must satisfy final_max >= final_rop >= final_min")
        return self


class ConfigUpdateRequest(BaseModel):
    rule_version: str = Field(min_length=1)
    updates: dict[str, object]


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    batch_id: int | None = None
    session_id: str | None = None


class ConfirmPendingRequest(BaseModel):
    """Turning a staged proposal into a real review decision.

    The engineer may correct the values at the point of confirming -- the agent
    parsed them from prose, so this is the moment a human takes ownership.
    """
    pending_id: int
    decision: Literal["override", "accept", "reject"] = "override"
    final_max: int | None = Field(default=None, ge=0)
    final_rop: int | None = Field(default=None, ge=0)
    final_min: int | None = Field(default=None, ge=0)
    comment: str = ""
    justification: str = ""


class CriticalityRequest(BaseModel):
    pattern: str = Field(min_length=2, description="machine_type substring")
    criticality: Literal["High", "Medium", "Low"]
    service_level_target: float | None = Field(default=None, ge=0.5, le=1.0)


class BulkReviewItem(BaseModel):
    item_id: str
    stockroom_id: str | None = None


class BulkReviewFilters(BaseModel):
    """Server-side selection of pending rows when no explicit item list is given.

    Powers 'accept every high-confidence agreement in this lane' without the
    client having to page the whole queue.
    """
    risk_level: Literal["Low", "Medium", "High"] | None = None
    action: Literal["Increase", "Maintain", "Decrease"] | None = None
    consumable: str | None = None
    route: str | None = None
    agreement: Literal["match", "diverge", "none"] | None = None
    reason_code: str | None = None
    min_exposure: float | None = Field(default=None, ge=0)
    min_confidence: float | None = Field(default=None, ge=0, le=1)
    exclude_high_risk: bool = True


class BulkReviewRequest(BaseModel):
    """Accept or reject many rows in one call.

    Only rows currently in pending_review are actioned; high-risk/override rows
    still land in awaiting_senior, so the two-person rule is preserved.
    """
    batch_id: int
    decision: Literal["accept", "reject"]
    comment: str = ""
    justification: str = ""
    items: list[BulkReviewItem] | None = None
    filters: BulkReviewFilters | None = None

    @model_validator(mode="after")
    def needs_target(self):
        if not self.items and self.filters is None:
            raise ValueError("provide items or filters to select rows")
        return self
