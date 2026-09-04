"""Pydantic request/response models."""

import re
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


class StagedAction(BaseModel):
    """What the chat agent staged for a human to press.

    Nothing here has been recorded. Confirming it is a click that calls the
    same endpoint the console tray calls (routers/review.py confirm_pending),
    under the engineer's own role -- which is why the agent staging one still
    cannot reach `review_history`.

    `executed` is a constant False by design. If it ever needs to be True, that
    belongs to the review router, not to a chat payload.
    """
    kind: Literal["confirm_pending", "discard_pending"]
    item_id: str
    batch_id: int | None = None
    stockroom_id: str | None = None
    pending_id: int | None = None
    proposed_max: int | None = None
    proposed_rop: int | None = None
    proposed_min: int | None = None
    executed: bool = False


class TriageRunRequest(BaseModel):
    """Triage one item, or the whole batch when item_id is omitted.

    Per item is the console path: triage is the only step that spends model
    calls, so it is paid for when an engineer opens a row, not up front for
    thousands of rows nobody reads.
    """
    batch_id: int = Field(ge=1)
    llm_call_budget: int = Field(default=2000, ge=3, le=2000)
    refresh: bool = False
    item_id: str | None = None
    stockroom_id: str | None = None


class SimilarityRunRequest(BaseModel):
    batch_id: int = Field(ge=1)
    refresh: bool = False


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


class PartCategoryRequest(BaseModel):
    """One lexicon rule: a regex over item_desc, and the category it implies.

    Lower priority wins, so a specific rule (sensor) must sit above a generic
    one (holder) -- "SENSOR BRACKET ASSY" is a sensor.
    """
    pattern: str = Field(min_length=2, max_length=200,
                         description="regex matched against item_desc, case-insensitive")
    category: str = Field(min_length=2, max_length=40)
    priority: int = Field(default=500, ge=1, le=9999)

    @model_validator(mode="after")
    def pattern_must_compile(self):
        # Rejected here rather than discovered mid-batch. load_rules() also
        # skips a broken pattern at read time, for rows that predate this check.
        try:
            re.compile(self.pattern)
        except re.error as e:
            raise ValueError(f"pattern is not a valid regular expression: {e}")
        return self


class DormantRuleRequest(BaseModel):
    """One dormant stocking rule: who it applies to, and what quantity it keeps.

    Scope alone decides which rule wins: item beats category beats default, and
    (scope, match_key) is unique, so at most one rule matches at each tier. No
    priority number to keep straight and nothing to renumber.
    """
    scope: Literal["item", "category", "default"]
    match_key: str = Field(default="", max_length=64,
                           description="item_id, category name, or '' for default")
    policy: Literal["hold_current", "fixed_qty", "zero"]
    fixed_qty: int | None = Field(default=None, ge=0, le=10_000)

    @model_validator(mode="after")
    def coherent(self):
        # A fixed_qty rule with no quantity would size to 0 -- the exact silent
        # zero this whole layer exists to stop. Rejected at the API, not
        # discovered mid-batch.
        if self.policy == "fixed_qty" and self.fixed_qty is None:
            raise ValueError("policy 'fixed_qty' requires fixed_qty")
        if self.scope == "default" and self.match_key:
            raise ValueError("the default rule takes no match_key")
        if self.scope != "default" and not self.match_key:
            raise ValueError(f"scope '{self.scope}' requires a match_key")
        return self


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
    assist_verdict: Literal["flag_for_review", "bulk_accept_candidate",
                            "needs_context"] | None = None
    assist_preselect: bool = False
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
