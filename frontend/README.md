# BOM Review Console — Frontend

Next.js 16 console that makes the backend workflow visible: ingest → score →
triage → review → two-person approval → WINGS export.

> The engine calculates. NYRA explains. The engineer approves.
> WINGS updates only after approval.

## Run

Both servers are needed — the frontend proxies everything to the backend.

```powershell
# terminal 1 — backend
.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --port 8011

# terminal 2 — frontend
cd frontend
npm run dev -- --port 3010     # http://localhost:3010
```

`BACKEND_URL` overrides the backend address (default `http://127.0.0.1:8011`).

## Test

```powershell
cd frontend
npx tsc --noEmit                                  # typecheck
npm run build                                     # production build
node scripts/e2e_console.mjs                      # 33 checks through the BFF proxy
node scripts/screenshot.mjs ./shots               # render every page, both themes
```

`e2e_console.mjs` drives the real Jan'26 CSV through the frontend's own proxy —
upload, score, filter, review, senior approval, export gate, chat guard.
`screenshot.mjs` catches what the API tests cannot: hydration failures, console
errors, horizontal overflow.

## Pages

| Route | What it shows |
|---|---|
| `/` | Batch list, ingest KPIs, CSV upload |
| `/batches/[id]` | Approval pipeline, reason-code distribution, filterable review queue |
| `/batches/[id]/items/[itemId]` | Engine proposal vs current, reason codes, input context, decision form, review history |
| `/config` | Versioned thresholds; propose/confirm machine criticality |
| `/chat` | Read-only retrieval stub |

## Architecture

**BFF proxy** — `src/app/api/backend/[...path]/route.ts` forwards every call to
FastAPI. This means no CORS configuration, and it is where Entra ID plugs in:
today the role comes from the client role-switcher; in production this route
reads the authenticated session and the client cannot choose its own role.

**Role switcher** (top right) sets `X-User`/`X-Role`. It exists so the RBAC and
two-person rules are *visible* — switch to `viewer` and actions disable; switch
to `senior` and approval unlocks; try approving your own review and it is
refused. It is **not** security.

**Design tokens** — `globals.css` holds the palette. The workflow funnel uses a
validated single-hue ordinal ramp; reason-code bars are sequential (one hue);
status and risk always pair an icon + label so colour never carries meaning
alone. Light and dark are both selected, not auto-flipped.

## Layout

```
frontend/src/
  app/
    layout.tsx                        shell + session provider
    page.tsx                          batches + upload
    batches/[id]/page.tsx             pipeline + review queue
    batches/[id]/items/[itemId]/      item detail + decision + history
    config/page.tsx                   thresholds + criticality
    chat/page.tsx                     retrieval stub
    api/backend/[...path]/route.ts    BFF proxy
  components/  Nav, ui (chips/tiles/banners), WorkflowPipeline
  lib/         api (fetch + errors), session (roles), types
scripts/       e2e_console.mjs, screenshot.mjs
```

## Environment notes

This network requires a proxy (`proxy-png.intel.com:912`) that Node does not
auto-detect. It is configured in `.npmrc`; without it `npm install` fails with
`ETIMEDOUT`. See `docs/Backend_Scaffold_Notes.md` §F1.

`next.config.ts` sets `allowedDevOrigins` — Next.js 16 blocks cross-origin dev
resources by default, which silently prevents hydration when the app is opened
on `127.0.0.1` instead of `localhost`. See §F5.
