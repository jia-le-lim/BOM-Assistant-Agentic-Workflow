# BOM Review Console — Frontend

Next.js 16 console that makes the backend workflow visible: ingest → score →
triage → review → two-person approval → WINGS export.

> The engine calculates. NYRA explains. The engineer approves.
> WINGS updates only after approval.

## Run

Both servers are needed — the frontend proxies everything to the backend.

```powershell
# terminal 1 — backend
make backend

# terminal 2 — frontend
cd frontend
npm run dev -- --webpack --port 3011     # http://localhost:3011
```

`BACKEND_URL` overrides the backend address (default `http://127.0.0.1:8012`).
`make dev` from the repository root starts both on ports 3011/8012 and sets
`BACKEND_URL` to the selected `BACKEND_PORT`. Docker uses ports 3010/8011.

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
| `/login` | Custom two-step email/username and password sign-in, account recall and inline errors |
| `/` | BOM review workspaces, cycle search, and workspace creation |
| `/workspaces/[id]` | Persisted review cycle, datasheet drop zone, ingestion and scoring |
| `/batches/[id]` | Approval pipeline, reason-code distribution, filterable review queue |
| `/batches/[id]/items/[itemId]` | Engine proposal vs current, reason codes, input context, decision form, review history |
| `/config` | Versioned thresholds; propose/confirm machine criticality |
| `/config/dormant` | Dormant stocking rules, coverage, editable bulk proposals |
| `/chat` | Full NYRA conversation, sources, and staged review actions |

## Workspaces

Create a workspace before uploading a BOM datasheet. Give the review cycle a
name and select its module, then drop in a CSV or Excel extract. Empty workspaces
are saved in the backend and can be reopened later. Each workspace uses one batch
ID for its datasheet, recommendations, and decisions; existing batches appear in
the workspace list automatically. Start a new workspace for the next cycle.

The existing batch schema stores empty workspaces with `status=draft`, so no
schema migration is required. Restart the backend to enable `POST /batches` and
the optional `batch_id` field on uploads. An uploaded workspace cannot be replaced.
If scoring fails after upload, retry recommendations in the same workspace.

`npm run check:workspaces` checks creation, persistence, drag/drop, cycle isolation,
errors, filters, mobile, and both themes against browser fixtures. Screenshots go
to `.local/workspaces`. Backend lifecycle tests are in `tests/test_workspaces.py`.

## Page-aware assistant

In the full `/chat` interface, type `@` or press the `@` button to choose a
workspace by name, module, or ID. Use the arrow keys and Enter/Tab, or click a
result. The selected workspace appears as a removable chip and scopes subsequent
questions. Saved conversations restore their last workspace; New chat clears
the focus. Without a selection, the first request uses the latest scored
workspace and then keeps that workspace for follow-ups. Draft workspaces can be
selected, but recommendations require an uploaded and scored datasheet.

`npm run check:chat-workspaces` checks the picker, keyboard interactions,
request context, saved focus, and responsive layout with isolated API fixtures.

### Chat skills

Type `/` at the start of a message, or press the `/` button, to search the skill
catalog. Arrow keys and Enter/Tab select a skill without sending it. Add the
arguments shown below the composer, then send. Choose a workspace with `@`
first, or add a workspace mention while composing (for example, `/brief @January`
and select January). Activity on the right shows the selected skill and its tool
calls, including when reopening saved conversations.

| Command | Result |
|---|---|
| `/brief` | Workspace status, pending exposure, and priorities |
| `/triage 10` | Up to 10 items still awaiting review, ranked by exposure (1–25) |
| `/explain 100005` | Current vs engine-proposed levels, reason codes, and procurement context |
| `/history 100005` | Recorded decisions and notes across review cycles |
| `/review-note 100005` | An unsaved draft based on the item's evidence |
| `/propose 100005 max 3 rop 2 min 1 reason demand review` | Staged proposal using the quantities supplied; still requires confirmation |
| `/dormant-check` | Confirmed rule coverage and inventory book-value comparison |
| `/peers 100005` | Comparable historical parts and evidence limitations |

Item commands except `/history` accept `stockroom <id>` for ambiguous items.
`/propose` requires at least one of `max`, `rop`, or `min`; omit quantities you
do not want to supply, and place `reason` last. `/brief` also handles draft
workspaces; other workspace skills require scoring. History and carried-forward
notes intentionally include earlier cycles. Proposals, dormant coverage, and
peer evidence retain their backend review-role requirements.

Instructions live in `backend/app/agent/skills/*/SKILL.md`; the backend serves
their descriptions through `GET /chat/skills`. Restart the backend after adding
or editing skills. `npm run check:skills` verifies the picker, workspace context,
activity, recovery, and desktop/mobile layout with isolated fixtures; screenshots
go to `.local/chat-skills`.

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
All Settings sections are private to your signed-in account. Threshold edits require admin rights. Proposals retain the normal role checks
and explicit confirmation by the account owner with senior/admin rights. A response arriving after navigation or form
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

**Pilot sign-in** uses the custom `/login` card, based on the supplied design
reference and the workspace's light/dark tokens. It has no React Bits Pro or
license dependency. Caddy verifies opaque, HttpOnly session cookies; the BFF
also checks the session before forwarding requests and derives the user on the
server. Passwords are checked against the pilot's existing bcrypt hashes.
The form remembers an identifier only. Sessions expire after eight hours or a
frontend restart, and the sidebar Sign out action revokes them immediately.

Set `PILOT_AUTH_REQUIRED=1` and `PILOT_AUTH_FILE` to the private hash-only account
file to enable authentication in a standalone frontend. The Docker deployment
sets both and mounts this file read-only. With neither set, localhost retains
the demo identities. See [pilot deployment instructions](../infra/supabase/PILOT.md#custom-sign-in-form)
for generating the file from existing accounts and enabling email aliases.

For local development on this PC, enable the same pilot credentials once:

```powershell
cd frontend
npm run setup:auth
```

The setup command reads `C:/ProgramData/BOM-Supabase/pilot-accounts.local.json`,
copies only usernames, email aliases if present, and bcrypt hashes into the
ignored `.local/pilot-auth.json`, then enables authentication in `.env.local`.
It preserves other environment settings and does not change the Docker deployment.
Next.js reloads this environment file while `make dev` is running; refresh
http://localhost:3011/login and use your existing pilot username and password.
On another machine, pass the account file as `npm run setup:auth -- "path/to/pilot-accounts.local.json"`.
Rerun setup after changing the pilot passwords or email aliases to refresh the
local hash copy. Neither passwords nor session cookies are committed to Git.

After `npm run build -- --webpack`, run `npm run check:auth` with Node 22.18+
to verify session expiry/revocation, failed sign-ins, identity spoofing, CSRF,
safe redirects, and the real browser flow against an isolated backend. It writes
dark/light screenshots to `.auth-check/` and never changes live pilot data.

**BFF proxy** — `src/app/api/backend/[...path]/route.ts` forwards every call to
FastAPI. This means no CORS configuration, and it is where Entra ID plugs in:
the authenticated session supplies both the username and the fixed `admin`
role. Browser headers cannot change either value when login is required.

**Workspace access** — every existing pilot login has Administrator access.
The sidebar displays the signed-in username and Administrator label, with
Sign out for account changes. Previous browser role selections are discarded.
Workspace lists, direct links, chat tools, history, uploads, reviews and exports
are restricted to workspaces created by the signed-in username. Administrator
access does not bypass ownership. Imported workspaces retain their recorded
owner; usernames and module names are not used to guess ownership.
The existing requirement for a different person to approve a review still applies.
With private workspaces, cross-account approvals are denied and those decisions
remain pending; explicit sharing would require a separate access model.
Header-based backend roles remain available for isolated development tests.

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
