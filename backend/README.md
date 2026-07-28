# BOM Review Assistant — Backend Scaffold

FastAPI implementation of PRD §7, wired to the backtested rule engine
(`analysis/engine/engine.py`, `rule_version 0.2.0-tcb`).

> The engine calculates. NYRA explains. The engineer approves.
> WINGS updates only after approval.

## Run

```powershell
# from repo root (venv already contains everything, see Offline install below)
.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --port 8011
# interactive docs: http://127.0.0.1:8011/docs
```

## Test

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest tests -q          # 17 tests, incl. real-file E2E
..\.venv\Scripts\python.exe scripts\e2e_smoke.py        # live HTTP workflow (server must be up)
```

## Endpoints (PRD §7)

| Endpoint | Role(s) | Purpose |
|---|---|---|
| `POST /upload-bom-file` | engineer/senior/admin/it | CSV → normalize → quarantine → persist |
| `POST /run-recommendation?batch_id=` | engineer/senior/admin/it | Score batch with the rule engine |
| `GET /recommendations?batch_id=` | any | Filter + paginate; `status=` derives workflow state |
| `GET /recommendations/{item_id}?batch_id=` | any | One item: result, status, context, latest review |
| `POST /review/{item_id}?batch_id=` | engineer/senior/admin | accept / override / reject + comment |
| `POST /review/{item_id}/approve?batch_id=` | senior/admin | Senior sign-off (overrides + High risk) |
| `GET /history/{item_id}` | any | Review history across batches (memory layer) |
| `GET /config/rules` · `POST /config/rules` | any · admin | Versioned thresholds; version must change |
| `POST /config/criticality` · `.../confirm` | reviewer · senior | Two-person criticality config |
| `GET /export/wings?batch_id=` | engineer/senior/planner/admin/it | Approved-changes-only WINGS CSV |
| `POST /chat` | any | Read-only NYRA stub: explain / history / top-exposure |

Identity is a **stub**: `X-User` / `X-Role` headers. See Backend_Scaffold_Notes.md —
this must become Intel SSO / Entra ID before any real deployment.

## Workflow states

`auto_cleared` → no change proposed, no risk flag — never exported.
`pending_review` → awaiting engineer (includes engine-unflagged rows whose values
would change: `REQUIRE_REVIEW_FOR_ALL_CHANGES` safety switch).
`awaiting_senior` → reviewed; override or High-risk needs a second person.
`reviewed` → final; exported only if values actually differ from current.

## Offline install (Intel network blocks pip)

pip cannot reach pypi.org directly, but HTTPS via `Invoke-WebRequest` works.
Wheels were fetched from the PyPI JSON API and installed with
`pip install --no-index --find-links <dir>`. Exact versions: `requirements.txt`.

## Layout

```
backend/
  app/
    main.py            app factory + routers
    config.py          paths, module filter, safety switches
    db.py              SQLite DDL + rule-config seeding (data mart stand-in)
    security.py        RBAC stub (X-Role headers)
    audit.py           audit_log writes (every write endpoint)
    ingestion.py       normalize + quarantine (analysis §7.4 fixes)
    engine_adapter.py  bridge to analysis/engine — the single source of rules
    services.py        status derivation + export assembly
    schemas.py         pydantic request models
    routers/           upload, recommend, review, rules_config, export, chat
  tests/               16 synthetic + 1 real-file E2E
  scripts/e2e_smoke.py live HTTP workflow driver
  data/                SQLite DB (gitignored)
```
