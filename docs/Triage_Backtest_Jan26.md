# January 2026 triage backtest

`analysis/s16_triage_backtest.py` ran the production statistical engine and the
compiled triage graph over both persisted January splits using `EchoProvider`.

| segment | review rows | divergences | divergences cleared | clear candidates | clear precision |
|---|---:|---:|---:|---:|---:|
| Consumable | 190 | 60 | 0 | 0 | n/a |
| Non-consumable | 2,549 | 93 | 0 | 2 | 100% |
| **Total** | **2,739** | **153** | **0** | **2** | **100%** |

The combined result clears the configured 98% precision bar, so guarded bulk
preselection is implemented. It remains disabled by default because two
positive clear candidates are not enough evidence to enable it automatically.

The current engine produces 60 persisted consumable divergences (61 before
ingestion quarantine), not the 66 in the original plan. The non-consumable
count remains 93. The report records current reproducible behavior rather than
forcing the older count.
