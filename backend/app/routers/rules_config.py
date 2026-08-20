"""Rule threshold config -- admin-editable, versioned, never hard-coded (PRD 6.2).

Also hosts the machine-criticality config (PRD 5.3): anyone with review rights
may PROPOSE a criticality (including an agent acting on engineer instruction),
but only an admin/senior CONFIRMS it, and the engine reads confirmed rows only.
"""

import json

from fastapi import APIRouter, Depends, HTTPException

from ..audit import audit
from ..db import active_config, get_conn, stored_config
from .. import engine_statistical, similarity
from ..part_category import categorise, load_rules
from ..schemas import (ConfigUpdateRequest, CriticalityRequest,
                       PartCategoryRequest)
from ..security import APPROVE_ROLES, CONFIG_WRITE_ROLES, REVIEW_ROLES, any_role, require_role

router = APIRouter()

# Statistical-engine auto-clear knobs (PRD v3 Phase 5). Shown with their engine
# defaults when a config has not overridden them, so the Config page always
# reflects the effective policy. All default OFF -- see s15 calibration.
AUTOCLEAR_DEFAULTS = {
    "autoclear_noop_abs": engine_statistical.AUTOCLEAR_NOOP_ABS,
    "autoclear_noop_rel": engine_statistical.AUTOCLEAR_NOOP_REL,
    "autoclear_immaterial_usd": engine_statistical.AUTOCLEAR_IMMATERIAL_USD,
    "autoclear_high_value_usd": engine_statistical.AUTOCLEAR_HIGH_VALUE_USD,
    "autoclear_reliable": engine_statistical.AUTOCLEAR_RELIABLE,
}

TRIAGE_DEFAULTS = {
    "triage_clear_min_confidence": 0.8,
    "triage_clear_precision_bar": 0.98,
    "triage_preselect_min_confidence": 0.9,
    "triage_guarded_assist_enabled": False,
}

# Advisory KNN peer-evidence knobs, shown with the module defaults so the Config
# page always reflects the policy actually in force.
SIMILARITY_DEFAULTS = {
    "similarity_max_distance": similarity.MAX_DISTANCE,
    "similarity_min_neighbours": similarity.MIN_NEIGHBOURS,
    "similarity_k": similarity.K_NEIGHBOURS,
    "similarity_divergence_frac": similarity.DIVERGENCE_FRAC,
}

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
    "autoclear_noop_abs": (int, float),
    "autoclear_noop_rel": (int, float),
    "autoclear_immaterial_usd": (int, float),
    "autoclear_high_value_usd": (int, float),
    "autoclear_reliable": (bool,),
    "triage_clear_min_confidence": (int, float),
    "triage_clear_precision_bar": (int, float),
    "triage_preselect_min_confidence": (int, float),
    "triage_guarded_assist_enabled": (bool,),
    "similarity_max_distance": (int, float),
    "similarity_min_neighbours": (int, float),
    "similarity_k": (int, float),
    "similarity_divergence_frac": (int, float),
}


@router.get("/config/rules")
def get_rules(actor: dict = Depends(any_role())):
    conn = get_conn()
    try:
        cfg = active_config(conn)
        merged = {**AUTOCLEAR_DEFAULTS, **TRIAGE_DEFAULTS,
                  **SIMILARITY_DEFAULTS, **cfg}
        return {"rule_version": cfg["rule_version"],
                "config": {k: v for k, v in merged.items() if not k.startswith("_")}}
    finally:
        conn.close()


@router.post("/config/rules")
def update_rules(body: ConfigUpdateRequest,
                 actor: dict = Depends(require_role(*CONFIG_WRITE_ROLES))):
    conn = get_conn()
    try:
        # stored, not active: active_config() has machine_criticality_config
        # merged in, and writing that back would freeze a copy of a table an
        # admin can still edit into this rule_version's immutable stamp.
        cfg = stored_config(conn)
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
        insert = ("INSERT INTO rule_config (rule_version, config_json, active, "
                  "updated_by) VALUES (?,?,1,?)")
        row = (body.rule_version, json.dumps(new_cfg), actor["user"])
        if conn.is_postgres:
            # Deactivate and insert in ONE statement. Split across two, a
            # failure in between leaves the table with NO active row, and
            # active_config() then raises on every request that reads the
            # rules -- which is all of them. The REST transport commits per
            # call, so that window is real there.
            conn.execute(
                "WITH deact AS (UPDATE rule_config SET active=0 WHERE active=1 "
                f"RETURNING config_id) {insert} RETURNING config_id", row)
        else:
            conn.execute("UPDATE rule_config SET active=0")
            conn.execute(insert, row)
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


# ---------------------------------------------------------------------------
# part-category lexicon (PRD 5.3 pattern: propose -> confirm -> engine reads)
# ---------------------------------------------------------------------------

@router.get("/config/part-categories")
def list_part_categories(actor: dict = Depends(any_role())):
    """Every rule, confirmed and pending. Only confirmed ones affect retrieval."""
    conn = get_conn()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT pattern, category, priority, set_by, confirmed, confirmed_by, "
            "updated_at FROM part_category_config ORDER BY priority, pattern")]
        return {"rules": rows, "confirmed": sum(1 for r in rows if r["confirmed"]),
                "pending": sum(1 for r in rows if not r["confirmed"])}
    finally:
        conn.close()


@router.post("/config/part-categories")
def propose_part_category(body: PartCategoryRequest,
                          actor: dict = Depends(require_role(*REVIEW_ROLES))):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO part_category_config (pattern, category, priority, "
            "set_by, confirmed, updated_at) VALUES (?,?,?,?,0,datetime('now')) "
            "ON CONFLICT(pattern) DO UPDATE SET category=excluded.category, "
            "priority=excluded.priority, set_by=excluded.set_by, "
            "confirmed=0, confirmed_by=NULL, updated_at=datetime('now')",
            (body.pattern, body.category, body.priority, actor["user"]))
        audit(conn, actor, "POST", "/config/part-categories", "part_category",
              body.pattern, body.model_dump())
        conn.commit()
        return {"pattern": body.pattern, "category": body.category,
                "priority": body.priority, "confirmed": False,
                "note": "proposal recorded; requires confirmation before "
                        "similarity uses it"}
    finally:
        conn.close()


@router.post("/config/part-categories/{pattern:path}/confirm")
def confirm_part_category(pattern: str,
                          actor: dict = Depends(require_role(*APPROVE_ROLES))):
    conn = get_conn()
    try:
        r = conn.execute("SELECT * FROM part_category_config WHERE pattern=?",
                         (pattern,)).fetchone()
        if r is None:
            raise HTTPException(404, f"no part-category rule for pattern '{pattern}'")
        if r["set_by"] == actor["user"]:
            raise HTTPException(403, "proposer cannot confirm their own rule")
        conn.execute(
            "UPDATE part_category_config SET confirmed=1, confirmed_by=?, "
            "updated_at=datetime('now') WHERE pattern=?", (actor["user"], pattern))
        audit(conn, actor, "POST", "/config/part-categories/confirm",
              "part_category", pattern, {"confirmed": True})
        conn.commit()
        return {"pattern": pattern, "confirmed": True,
                "confirmed_by": actor["user"]}
    finally:
        conn.close()


@router.get("/config/part-categories/coverage")
def part_category_coverage(batch_id: int, limit: int = 20,
                           actor: dict = Depends(require_role(*REVIEW_ROLES))):
    """How much of a batch the lexicon actually resolves, and what it missed.

    The feedback loop that keeps the lexicon alive: an engineer reads the
    unmatched descriptions and writes the next rule.
    """
    conn = get_conn()
    try:
        if conn.execute("SELECT 1 FROM batches WHERE batch_id=?",
                        (batch_id,)).fetchone() is None:
            raise HTTPException(404, f"batch {batch_id} not found")
        rules, broken = load_rules(conn)
        by_category: dict[str, int] = {}
        samples: list[str] = []
        total = uncategorised = 0
        for row in conn.execute(
                "SELECT payload FROM bom_rows WHERE batch_id=? AND quarantined=0",
                (batch_id,)):
            desc = json.loads(row["payload"]).get("item_desc")
            total += 1
            cat = categorise(desc, rules)
            if cat:
                by_category[cat] = by_category.get(cat, 0) + 1
            else:
                uncategorised += 1
                if len(samples) < max(1, min(int(limit), 100)) and str(desc or "").strip():
                    samples.append(str(desc).strip()[:80])
        return {"batch_id": batch_id, "total": total,
                "categorised": total - uncategorised,
                "uncategorised": uncategorised,
                "pct": round(100 * (total - uncategorised) / total, 1) if total else 0.0,
                "by_category": dict(sorted(by_category.items(),
                                           key=lambda kv: -kv[1])),
                "uncategorised_samples": samples,
                "broken_rules": broken}
    finally:
        conn.close()
