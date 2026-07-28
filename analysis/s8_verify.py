import sys, pandas as pd
from pathlib import Path
sys.path.insert(0,str(Path("engine")))
import engine
from common import load_typed
df=load_typed("TCB")
full=engine.run(df)
stripped=engine.run(df.drop(columns=[c for c in engine.FORBIDDEN if c in df.columns]))
same=full.equals(stripped)
print(f"LEAKAGE CHECK: output identical with label columns removed entirely: {same}")
print(f"  forbidden cols present in input: {sorted(c for c in engine.FORBIDDEN if c in df.columns)}")
d1=engine.run(df); d2=engine.run(df)
print(f"DETERMINISM CHECK (PRD s10): two runs identical: {d1.equals(d2)}")
print(f"\nrule_version={full.rule_version.iloc[0]}  model_version={full.model_version.iloc[0]}")
print("\nsample output rows (the trap population):")
s=full[full.rc_ZERO_RECOMMENDATION_OVERRIDE].head(3)
for i,r in s.iterrows():
    print(f"\n  item {r.item_id} | max {df.loc[i,'max_qty']} -> {r.factory_recommended_new_max} | "
          f"review={r.review_required} risk={r.risk_level} conf={r.confidence_score}")
    print(f"    codes: {r.reason_code[:110]}")
    print(f"    why  : {r.explanation[:160]}")
