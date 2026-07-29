"""ML serving seam -- deliberately empty of a model.

State of play (docs/Feature_Selection_TCB_Jan26.md)
--------------------------------------------------
Feature selection is finished and a defensible five-feature set is chosen:
`max_qty`, `machine_type`, `vf_avail_qty`, `has_activity_record`,
`qry_msbidatadays_since_effective` -- 57.4% out-of-fold log-loss reduction on
the binarised `factory_recommended_new_max > 0` target. Nothing is trained.

Two constraints shape what this seam is allowed to do:

1. The model may only ever produce a TRIAGE PROBABILITY -- "this row deserves
   a human" -- never a quantity. docs/Engine_Backtest_TCB_Jan26.md section 1 is
   unambiguous: on quantity the engine scores 77.7% exact versus 85.1% for
   doing nothing. A model that proposes numbers would make the product worse.

2. Jan'26 is one month, one reviewer (97.5% `pengchin`), 682 positives. A model
   trained on it today would substantially learn one person's January. The
   conversation_turn / item_note capture built in this change is what
   accumulates the multi-month, multi-reviewer labels that fix it.

So `triage_score` returns None until an artifact exists, `model_version` stays
"rules-only", and the engine is untouched. When a model does land, predictions
are logged to `model_prediction_log` (PRD 5.3) so they are auditable from day
one rather than retrofitted.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

MODEL_DIR = Path(os.environ.get(
    "BOM_MODEL_DIR",
    Path(__file__).resolve().parents[1] / "models"))

# Bumped only when a real artifact is served. The engine writes this into
# recommendation_result.model_version, so it is part of the audit trail.
RULES_ONLY = "rules-only"


def available() -> str | None:
    """Path of the active model artifact, or None. None is the current state."""
    if not MODEL_DIR.exists():
        return None
    artifacts = sorted(MODEL_DIR.glob("triage_max_v*.pkl"))
    return str(artifacts[-1]) if artifacts else None


def triage_score(df: pd.DataFrame) -> pd.Series | None:
    """P(this row needs a human), aligned to df.index. None when no model.

    Callers must treat None as "use the rules alone" rather than defaulting to
    zero -- a missing model is not a confident negative.
    """
    path = available()
    if path is None:
        return None

    import pickle

    with open(path, "rb") as fh:
        model = pickle.load(fh)
    return pd.Series(model.predict_proba(df)[:, 1], index=df.index)


def log_predictions(conn, batch_id: int, scores: pd.Series,
                    item_ids: pd.Series, model_version: str) -> None:
    """PRD section 5.3 model_prediction_log. Governance requires that a served
    prediction be reconstructable later, so this is written at scoring time."""
    conn.executemany(
        "INSERT INTO model_prediction_log (batch_id, item_id, model_version, "
        "prediction, features_hash) VALUES (?,?,?,?,?)",
        [(batch_id, str(i), model_version, float(s), "")
         for i, s in zip(item_ids, scores)])


def describe() -> dict:
    """Surfaced on /health so it is obvious which model, if any, is live."""
    path = available()
    return {"model": Path(path).name if path else None,
            "model_version": Path(path).stem if path else RULES_ONLY,
            "serves_quantities": False,
            "note": "triage probability only; quantities stay with the engine "
                    "and the engineer"}
