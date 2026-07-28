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


class CriticalityRequest(BaseModel):
    pattern: str = Field(min_length=2, description="machine_type substring")
    criticality: Literal["High", "Medium", "Low"]
    service_level_target: float | None = Field(default=None, ge=0.5, le=1.0)
