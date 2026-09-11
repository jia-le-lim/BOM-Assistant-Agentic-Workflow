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
npm run check:assistant                           # isolated browser checks; frontend only
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
| `/config/dormant` | Dormant stocking rules, coverage, editable bulk proposals |
| `/chat` | Full NYRA conversation, sources, and staged review actions |

## Page-aware assistant

The floating NYRA button appears on every page except `/chat`, which already
contains the full assistant. It keeps the conversation while navigating and
sends a fresh snapshot with each message: route, batch/item, visible sections
and text, selected text, the last focused field, and unsaved settings. The
"Viewing" line shows the current section; this uses the viewport and interaction,
not eye tracking. Password, hidden, email, and file inputs are excluded.

On Dormant rules, paste part IDs with their policies and quantities to fill a
draft. Tables can have mixed policies, for example:

```csv
item_id,policy,quantity
000123,hold_current,
ABC-12,fixed_qty,4
000456,zero,
```

The assistant also fills rule thresholds, toggles, new version labels, machine
criticality (`machine_type,criticality`), and part categories
(`pattern,category,priority`). A real NYRA provider handles natural language;
the offline Echo provider supports explicit tables and `setting_name=value`.
Messages accept up to 20,000 characters and one draft can contain up to 500 rows.
Missing policies or fixed quantities require clarification.

Filling changes the editable form only. Review it and press Propose or Save;
bulk proposals retain failed rows for correction without resubmitting successes.
Threshold edits require admin rights. Proposals retain the normal role checks
and separate senior confirmation. A response arriving after navigation or form
edits cannot overwrite the newer state. Engine results change on the next run.

`npm run check:assistant` runs Playwright with intercepted API fixtures, so it
never changes live data. Set `BASE_URL` to the frontend URL if needed. On Windows
the test falls back to installed Edge when Playwright Chromium is unavailable.
The screenshot is written to `.assistant-check/dormant-rules.png`.

The backend accepts optional `page_context` on both `/chat` and `/chat/stream`
and returns typed `page_actions`. Restart a backend running without reload after
updating it. With `LLM_REDACT_PROMPTS=1`, raw page text, selections, and form state
are withheld from the provider; structured record tools retain their redaction.

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
