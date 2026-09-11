# TCB historical archive import - 11 September 2026

Imported TCB records from `BOM table/BOM Review Archive 1.csv`, skipping existing
item + stockroom + review-cycle identities. Existing database records remained
unchanged. The source archive and detailed audit contain business data and are
kept locally outside version control.

| Result | Rows |
|---|---:|
| TCB source records | 20,120 |
| Already present, skipped | 2,723 |
| New BOM records | 17,397 |
| Historical reviews added | 17,307 |
| Invalid consumption records quarantined | 90 |
| New duplicate historical identities | 0 |

| Review cycle | New BOM records | Historical reviews | Quarantined |
|---|---:|---:|---:|
| June 2025 | 2,813 | 2,786 | 27 |
| August 2025 | 2,957 | 2,943 | 14 |
| October 2025 | 2,962 | 2,949 | 13 |
| January 2026 | 237 | 236 | 1 |
| March 2026 | 2,765 | 2,752 | 13 |
| May 2026 | 2,788 | 2,776 | 12 |
| August 2026 | 2,875 | 2,865 | 10 |

January already contained 2,723 matching identities; only its missing records
were inserted. One pre-existing duplicate historical identity was preserved;
verification confirmed that the import introduced none.

The [import utility](../../backend/scripts/import_tcb_archive.py) prepares data
through the existing ingestion, scoring and history functions in an isolated
SQLite staging database. It records source fingerprints, builds insert-only SQL,
and applies it in one PostgreSQL transaction with locks and fingerprint checks.
Verification compares inserted records with prepared records and confirms that
the original records are unchanged. Source review dates and reviewers are retained.

The completed audit is in
`analysis/output/archive_tcb_import_20260911_final/`: `plan.json`,
`verification.json`, `before.db`, `prepared.db` and `apply.sql`. At verification,
the application held 26,880 BOM rows and 26,361 historical reviews.

The importer exposes separate `prepare`, `apply` and `verify` commands. Use a
fresh output directory when preparing another import; the prepared transaction
rejects a live database that changed after its snapshot. Later calibration and
SBA/TSB experiments use the expanded history without changing production settings.
