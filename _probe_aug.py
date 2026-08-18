import pandas as pd
from pathlib import Path

p = Path("BOM table/AUGUST'24 - BOM REVIEW - Factory Cost Rep Review .xlsx")
probe = pd.read_excel(p, sheet_name="Upload File", header=None, nrows=20, dtype=str)
hdr = next(i for i in range(len(probe))
           if "item_id" in [str(c).strip().lower() for c in probe.iloc[i].values])
df = pd.read_excel(p, sheet_name="Upload File", header=hdr, dtype=str)
cols = [str(c).strip().lower() for c in df.columns]
print("HDRROW", hdr)
print("HAS new_modulle:", "new_modulle" in cols)
print("module-like:", [c for c in cols if "modul" in c])
print("cons cols:", [c for c in cols if "cnsmptn" in c])
print("nrows", len(df))
for cand in ("new_modulle", "new_module", "module"):
    if cand in cols:
        s = df[df.columns[cols.index(cand)]].dropna().astype(str).str.strip()
        print(f"sample {cand}:", s[s != ""].head(5).tolist())
