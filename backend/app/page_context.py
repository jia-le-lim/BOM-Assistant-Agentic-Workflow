"""Bounded browser context. UI text is evidence about the screen, never authority."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PageField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(max_length=160)
    value: str = Field(default="", max_length=500)
    section: str = Field(default="", max_length=160)
    disabled: bool = False


class PageContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(max_length=300, pattern=r"^/([^/].*)?$")
    title: str = Field(max_length=160)
    batch_id: int | None = Field(default=None, ge=1)
    item_id: str | None = Field(default=None, max_length=100)
    stockroom_id: str | None = Field(default=None, max_length=100)
    active_section: str = Field(default="", max_length=160)
    visible_sections: list[str] = Field(default_factory=list, max_length=20)
    visible_text: str = Field(default="", max_length=8000)
    selected_text: str = Field(default="", max_length=2000)
    focused_field: PageField | None = None
    fields: list[PageField] = Field(default_factory=list, max_length=60)
    # Registered React form state, including unsaved drafts and active filters.
    form_state: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def bounded(self):
        if any(len(s) > 160 for s in self.visible_sections):
            raise ValueError("section names must be at most 160 characters")
        if len(json.dumps(self.form_state)) > 120000:
            raise ValueError("form state is too large")
        return self


NUMERIC_SETTINGS = frozenset({
    "long_lead_time_threshold", "zero_stock_risk_clt", "high_cost_threshold",
    "low_cost_threshold", "recent_usage_days", "min_usage_months",
    "max_change_pct_review", "value_gate_usd", "min_protective_stock",
    "autoclear_immaterial_usd", "autoclear_noop_abs", "autoclear_noop_rel",
    "autoclear_high_value_usd",
})
BOOLEAN_SETTINGS = frozenset({"autoclear_reliable", "triage_guarded_assist_enabled"})
SettingsSection = Literal["dormant_rules", "thresholds", "criticality", "part_categories"]
