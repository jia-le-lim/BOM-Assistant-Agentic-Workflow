"""Rule threshold config -- admin-editable, versioned, never hard-coded (PRD 6.2).

Also hosts the machine-criticality config (PRD 5.3): anyone with review rights
may PROPOSE a criticality (including an agent acting on engineer instruction),
but only an admin/senior CONFIRMS it, and the engine reads confirmed rows only.
"""

import json

from fastapi import APIRouter, Depends, HTTPException

from ..audit import audit
from ..db import active_config, get_conn
from ..schemas import ConfigUpdateRequest, CriticalityRequest
from ..security import APPROVE_ROLES, CONFIG_WRITE_ROLES, REVIEW_ROLES, any_role, require_role

router = APIRouter()

# Keys that may be updated via the API, with their expected types.
EDITABLE = {
    "low_cost_threshold": (int, float),
    "high_cost_threshold": (int, float),
    "long_lead_time_threshold": (int, float),
    "zero_stock_risk_clt": (int, float),
    "recent_usage_days": (int, float),
    "min_usage_months": (int, float),
    "max_change_pct_review": (int, float),
    "value_gate_usd": (int, float),
    "min_protective_stock": (int, float),
    "rule9_mode": (str,),
    "autoclear_guard": (bool,),
    "quantity_anchor": (str,),
}


@router.get("/config/rules")
def get_rules(actor: dict = Depends(any_role())):
    conn = get_conn()
    try:
        cfg = active_config(conn)
        return {"rule_version": cfg["rule_version"],
                "config": {k: v for k, v in cfg.items() if not k.startswith("_")}}
    finally:
        conn.close()


@router.post("/config/rules")
def update_rules(body: ConfigUpdateRequest,
                 actor: dict = Depends(require_role(*CONFIG_WRITE_ROLES))):
    conn = get_conn()
    try:
        cfg = active_config(conn)
        if body.rule_version == cfg["rule_version"]:
            raise HTTPException(400, "rule_version must change when config changes "
                                     "(determinism audit trail, PRD section 10)")
        unknown = [k for k in body.updates if k not in EDITABLE]
        if unknown:
            raise HTTPException(400, f"unknown/non-editable keys: {unknown}")
        for k, v in body.updates.items():
            if not isinstance(v, EDITABLE[k]) or isinstance(v, bool) and bool not in EDITABLE[k]:
                raise HTTPException(400, f"{k}: expected {EDITABLE[k]}, got {type(v).__name__}")

        new_cfg = {**cfg, **body.updates, "rule_version": body.rule_version}
        conn.execute("UPDATE rule_config SET active=0")
        conn.execute(
            "INSERT INTO rule_config (rule_version, config_json, active, updated_by) "
            "VALUES (?,?,1,?)",
            (body.rule_version, json.dumps(new_cfg), actor["user"]))
        audit(conn, actor, "POST", "/config/rules", "rule_config", body.rule_version,
              {"updates": body.updates})
        conn.commit()
        return {"rule_version": body.rule_version, "applied": body.updates}
    finally:
        conn.close()


@router.post("/config/criticality")
def propose_criticality(body: CriticalityRequest,
                        actor: dict = Depends(require_role(*REVIEW_ROLES))):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO machine_criticality_config (pattern, criticality, "
            "service_level_target, set_by, confirmed, updated_at) "
            "VALUES (?,?,?,?,0,datetime('now')) "
            "ON CONFLICT(pattern) DO UPDATE SET criticality=excluded.criticality, "
            "service_level_target=excluded.service_level_target, set_by=excluded.set_by, "
            "confirmed=0, confirmed_by=NULL, updated_at=datetime('now')",
            (body.pattern, body.criticality, body.service_level_target, actor["user"]))
        audit(conn, actor, "POST", "/config/criticality", "criticality", body.pattern,
              body.model_dump())
        conn.commit()
        return {"pattern": body.pattern, "criticality": body.criticality,
                "confirmed": False,
                "note": "proposal recorded; requires confirmation before the engine uses it"}
    finally:
        conn.close()


@router.post("/config/criticality/{pattern}/confirm")
def confirm_criticality(pattern: str,
                        actor: dict = Depends(require_role(*APPROVE_ROLES))):
    conn = get_conn()
    try:
        r = conn.execute("SELECT * FROM machine_criticality_config WHERE pattern=?",
                         (pattern,)).fetchone()
        if r is None:
            raise HTTPException(404, f"no criticality proposal for pattern '{pattern}'")
        if r["set_by"] == actor["user"]:
            raise HTTPException(403, "proposer cannot confirm their own proposal")
        conn.execute(
            "UPDATE machine_criticality_config SET confirmed=1, confirmed_by=?, "
            "updated_at=datetime('now') WHERE pattern=?", (actor["user"], pattern))
        audit(conn, actor, "POST", f"/config/criticality/{pattern}/confirm",
              "criticality", pattern, {"confirmed": True})
        conn.commit()
        return {"pattern": pattern, "confirmed": True, "confirmed_by": actor["user"]}
    finally:
        conn.close()
