"""Split Jan'26 TCB parts into two CSVs, preserving the source format.

Consumable (continuous moving)      = aging_status == "Active Moving"
Non-consumable (dead/less moving)   = everything else (Dead + New Part)

Rows and columns are kept exactly as in the source (raw strings, original
column order); only the module/aging_status filters are applied.
"""

from pathlib import Path

import common

OUT_CONSUMABLE = common.OUT / "tcb_consumable_moving.csv"
OUT_NONCONSUMABLE = common.OUT / "tcb_nonconsumable_dead.csv"


def main() -> None:
    df = common.load_raw()
    df.columns = [c.strip() for c in df.columns]

    tcb = df[df["module"].str.strip() == common.MODULE].copy()
    aging = tcb["aging_status"].str.strip()

    consumable = tcb[aging == "Active Moving"]
    nonconsumable = tcb[aging != "Active Moving"]

    consumable.to_csv(OUT_CONSUMABLE, index=False, encoding="utf-8-sig")
    nonconsumable.to_csv(OUT_NONCONSUMABLE, index=False, encoding="utf-8-sig")

    print(f"TCB rows total       : {len(tcb)}")
    print(f"Consumable (moving)  : {len(consumable)} -> {OUT_CONSUMABLE.name}")
    print(f"Non-consumable (dead): {len(nonconsumable)} -> {OUT_NONCONSUMABLE.name}")


if __name__ == "__main__":
    main()
