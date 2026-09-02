"""The REVIEW SCORECARD rule, in one place.

A strict 10% relative tolerance on Max and ROP, no absolute floor, Min excluded.
An engineer's 2 admits only 2 (1.8-2.2); an engineer's 0 admits only 0.

Deliberately NOT `engine_statistical._agreement`, which still uses max(1, 10%)
across all three fields for reason codes and confidence. s19 emits both so the
difference stays visible instead of one silently replacing the other.

The tolerance comes from the engine, so this cannot drift from it -- and there
is now one copy of that guarantee rather than one per script.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.engine_statistical import AGREE_TOL  # noqa: E402

__all__ = ["AGREE_TOL", "num", "pair", "matched"]


def num(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df.get(col), errors="coerce")


def pair(out: pd.DataFrame) -> np.ndarray:
    """Max and ROP only -- Min is a derived floor, not a replenishment lever."""
    return np.c_[out.factory_recommended_new_max,
                 out.factory_recommended_new_rop].astype(float)


def matched(engine: np.ndarray, bench: np.ndarray) -> np.ndarray:
    """Vectorised engine_statistical.close_enough over Max and ROP."""
    return (np.abs(engine - bench) <= AGREE_TOL * np.abs(bench)).all(axis=1)
