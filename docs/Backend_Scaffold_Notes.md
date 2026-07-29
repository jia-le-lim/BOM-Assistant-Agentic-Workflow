# Backend Scaffold — Build Log, Problems & Decisions

| | |
|---|---|
| Document type | Engineering notes (problems faced + decisions taken) |
| Date | 28 July 2026, updated 29 July 2026 (§7) |
| Backend | `backend/` — FastAPI 0.140.7, Python 3.12.10, **Supabase Postgres** (SQLite is test-only, §F9) |
| Engine | `analysis/engine/engine.py` `rule_version 0.2.0-tcb` (unchanged — the backend never re-implements rules) |
| Test status | **73/73 passing** offline + live HTTP smoke test on the real Jan'26 file |

---

## 1. Problems faced (in the order they occurred)

### P1 — pip cannot reach PyPI ✅ ROOT-CAUSED AND FIXED (see §F1)

`pip install` timed out against `pypi.org` while `Invoke-WebRequest` to the same
host succeeded. I worked around it by hand-downloading wheels and installing with
`pip install --no-index --find-links`. Two follow-on failures during resolution:

- fastapi requires `annotated-doc`, not on my initial list → second fetch round.
- pydantic 2.13.4 **exact-pins** `pydantic-core==2.46.4`, but "latest" is 2.47.0
  → had to fetch the pinned version and delete the newer wheel.

**This diagnosis was incomplete.** The network is not blocking pip — there is a
**WPAD-discovered proxy that Python and Node do not auto-detect**. Root cause and
permanent fix in §F1; the wheel-downloading workaround is no longer needed.
All 28 pinned versions remain in `backend/requirements.txt`.

### P2 — `pandas.itertuples()` silently renames underscore columns 🐛 caught pre-test

Engine output includes `_exposure_usd`. `itertuples()` renames
underscore-prefixed columns positionally (namedtuple rules), which would have
mis-read fields without an error. Persistence rewritten to explicit column
selection + `.to_numpy()`. Same class of bug as the pandas-3.0 `str`-dtype issue
hit during analysis: **pandas 3.x sharp edges are a recurring cost in this repo.**

### P3 — synthetic fixture tripped a real rule 🧪 test design, not a bug

Test row r8 (max 1 → proposed 2) was meant to be "changed but unflagged"; the
engine correctly fired `MAX_CHANGE_EXCEEDS_THRESHOLD` (2 > 1.5×1). Fixture
adjusted to 2 → 3 (under the 50% threshold). Note for future fixture authors:
at small quantities almost any increase breaches +50%, so the threshold rule
dominates low-count items. That may itself be worth an engineer discussion —
+1 unit on a max of 1 is a 100% change but a trivial decision.

### P4 — duplicate-key rows cannot share a DB primary key

Quarantined duplicate rows would collide on `(batch_id, item_id, stockroom_id)`.
Rows are suffixed `#dupN` in storage so every offending row is preserved and
visibly quarantined rather than silently dropped.

### P5 — starlette deprecation warning in tests ⚪ cosmetic, pinned versions

`Using httpx with starlette.testclient is deprecated; install httpx2`.
Harmless at these pins; will matter on the next starlette upgrade.

### P6 — real-file quarantine revealed overlap

All 12 broken-ladder rows in TCB are the **same rows** as the 12
negative-consumption rows (union = 12, not 24). The Phase-0 DQ scorecard listed
them as two separate 12-row findings; they are one defect population.

---

## 2. Policy questions surfaced by testing (need owner decisions)

### Q-A — should *reject* on a High-risk item need senior approval?

Live smoke test: engineer **rejects** a High-risk recommendation (keep current
values, export nothing) → scaffold routes it to `awaiting_senior`.

Current rule: senior approval for `override OR risk_level=High`, regardless of
direction. Defensible — declining protective stock on a high-risk part is
exactly the recom=0-trap decision in reverse — but PRD §3 says senior approves
"overrides and high-risk *changes*", and a reject changes nothing. **Owner call
needed.** One-line change either way (`review.py`, `requires_senior` predicate).

### Q-B — the safety switch adds only 6 rows

`REQUIRE_REVIEW_FOR_ALL_CHANGES` (auto-adopt nothing) put 6 rows into the queue
beyond the engine's 1,058 review-Y rows (1,064 total pending). Cheap insurance —
recommend keeping it on for Phase 1.

### Q-C — review throughput arithmetic

1,064 pending rows per cycle at (say) 1–2 min/row ≈ 2–4 working days — versus
the 4 days one engineer spent on all 2,780. The engine only pays for itself if
the queue is worked **by exposure order** (top 100 items = 75.6% of value).
The default sort does this, but the UI must not let bulk-accept flatten it.

---

## 3. Deliberate deviations from the PRD (documented, reversible)

| PRD says | Scaffold does | Why |
|---|---|---|
| §4 SQL data mart | ~~SQLite file~~ → **Supabase Postgres** (29 Jul 2026) | Resolved. SQLite remains only as the offline test backend; the app refuses to start without `DATABASE_URL` (§F9) |
| §5.3 `item_master` / `inventory_snapshot` / `consumption_snapshot` | Single `bom_rows` (key cols + full-row JSON payload) | Source is one 116-col workbook; normalization adds cost now, value only when WINGS/SFM connectors exist. Engine gets full input fidelity |
| §5.3 `model_prediction_log` | Created, empty | Table exists so predictions are auditable from the first one; no model is trained yet (`app/scoring.py`) |
| §10 Intel SSO / Entra ID | `X-User`/`X-Role` header stub | **Not authentication.** Route authorization, audit attribution and approval flow are real and tested; identity is a placeholder. Must be replaced before deployment |
| §7 `POST /chat` via NYRA | Deterministic intent parser | Read-only by construction; answers "I don't know" when no tool matches (PRD §8 hard control). LLM layer slots in behind the same tool surface |
| §7 Excel upload | CSV only | Avoids an openpyxl dependency through the offline-pip pipe; convert upstream |

Extra beyond PRD: **two-person criticality config** — anyone with review rights
(including an agent on engineer instruction) can *propose* machine criticality;
a different person with senior rights must *confirm*; the engine reads confirmed
rows only. This implements the architecture guardrail from the Phase-0 report
(§9.1): an LLM may write config, a human confirms, the engine stays deterministic.

Known determinism gap: confirming a criticality row changes engine input without
bumping `rule_version`. Mitigated by storing a `scored_config_hash` per batch
(criticality is merged into the hashed config), so any batch can prove which
effective config scored it. A future fix should fold criticality into the
versioned config record itself.

---

## 4. What was tested and the results

### Synthetic suite (16 tests)

- **Ingestion**: typo renames (`new_modulle`→`new_module`), casing fix
  (`maintain algo`→`Maintain Algo`), whitespace stripping, module filter,
  quarantine (negative consumption, duplicate keys), required-column and
  file-type rejection, RBAC on upload.
- **Leakage tripwire**: every fixture row carries poisoned PRD §5.1 output
  columns (`factory_recommended_new_max=999`, `justification="LEAK_IF_USED"`).
  Engine output never echoes them.
- **Workflow**: status derivation (3 auto_cleared / 4 pending on the fixture),
  accept→senior-approve, override→senior-approve, reject, self-approval
  blocked (403), viewer blocked (403), invalid override ordering blocked (422).
- **Export**: only approved changed rows leave (2 of 7); pending and
  awaiting-senior counted in headers; engineer-override values exported, not
  engine values.
- **Config**: version-must-change enforced, unknown keys rejected, non-admin
  rejected, re-scoring stamps the new `rule_version` on results.
- **Criticality**: unconfirmed proposals invisible to the engine;
  proposer-cannot-confirm enforced.
- **Chat**: explain/history/top-exposure answered with sources; anything else →
  "I don't know" with empty sources.
- **Audit**: batch/review/chat entities present with correct user attribution.

### Real Jan'26 E2E (in-process)

| Step | Result |
|---|---|
| Upload (11 MB, 17,165 rows → TCB filter) | 1.0 s, 2,780 loaded |
| Quarantine | 12 rows, all `NEGATIVE_CONSUMPTION` |
| Score | **0.6 s** for 2,768 rows (PRD §10 sub-second ✅ at batch level) |
| review_required = Y | 1,058 (backtest said 1,070 on 2,780 — the 12 quarantined rows were review-Y `DATA_QUALITY` rows, now handled at ingestion instead) |
| Determinism | Re-score → byte-identical result set ✅ |
| Export with no reviews | 0 rows exported, 1,064 pending ✅ nothing leaves without approval |

### Live HTTP smoke (uvicorn, port 8011)

Full workflow over the wire: health → upload (3.2 s) → score (1.1 s) → exposure-
sorted queue (top item $288,000, High) → accept → senior approve → reject →
export (1 row: `500840315, 3/2/1 → 12/10/0, accept, alice, boss, 0.2.0-tcb`) →
chat explain → chat "I don't know" guard. All correct.

---

## 5. Not built (scaffold boundary)

- Power BI, Power Automate (the review UI is now `frontend/`, see §6)
- Real NYRA/RAG (§8) — stub keeps the contract
- Excel ingestion, multi-stockroom logic, WINGS API (Phase 3)
- Background jobs/pagination-in-SQL for the status filter (fine at 2.8k rows,
  flagged in `recommend.py` for multi-module scale)

---

# Frontend Console — Build Log (Next.js 16)

`frontend/` — Next.js 16.2.12, React 19.2.4, Tailwind 4, TypeScript.
Docs: [`frontend/README.md`](../frontend/README.md).

## 6. Problems faced

### F1 — the real root cause of P1: an undetected WPAD proxy ✅ FIXED

`npm ping` failed with `ETIMEDOUT`, exactly like pip — but `Invoke-WebRequest`
reached `registry.npmjs.org` fine. That asymmetry is the diagnostic: **.NET
resolves a proxy that Python and Node do not.**

```powershell
[System.Net.WebRequest]::GetSystemWebProxy().GetProxy("https://registry.npmjs.org")
# -> http://proxy-png.intel.com:912/
```

Discovered via **WPAD** (`wpad` resolves to `10.11.109.85`); `netsh winhttp show
proxy` reports "Direct access" and every Internet Settings registry key is empty,
so nothing static reveals it. A raw TCP connect to `registry.npmjs.org:443` fails
outright — everything must traverse the proxy.

Fixed permanently for both toolchains:

| Tool | Config written |
|---|---|
| npm | `~/.npmrc` — `proxy`, `https-proxy`, `noproxy=localhost,127.0.0.1,.intel.com` |
| pip | `%APPDATA%\pip\pip.ini` — `[global] proxy = http://proxy-png.intel.com:912` |

`pip install` and `npm install` now work normally with no env vars. **This
retroactively removes the P1 workaround** — future dependencies install directly.

### F2 — `pip.ini` written with a UTF-8 BOM 🐛

PowerShell's `Out-File -Encoding utf8` emits a BOM; pip then parses the section
header as `'﻿[global]'` and fails. Fixed with
`[System.IO.File]::WriteAllText(path, text, UTF8Encoding($false))`. Worth
remembering for any config file this environment writes.

### F3 — Next.js 16 removed synchronous `params` (breaking)

`frontend/AGENTS.md` warns that this Next.js differs from training data, so I
read the bundled docs in `node_modules/next/dist/docs/` first. Confirmed:
`params` and `searchParams` are now **Promises** in `page`/`layout`/`route`.
Route handlers take `ctx: { params: Promise<…> }`; client pages unwrap with
React `use()`. Also relevant: Turbopack is default, and `next lint` is removed.

### F4 — `next/font/google` fetches at build time

The default template imports Geist from Google Fonts, which this network blocks.
Removed it — the palette specifies `system-ui` anyway, so nothing was lost.

### F5 — silent hydration failure via `allowedDevOrigins` ⚠️ the expensive one

Every page returned **HTTP 200**, the SSR HTML looked right, **no console error,
no page error** — but `/config` and the item page sat on "Loading…" forever and
no `/api/` request was ever made from the browser.

Cause: **Next.js 16 blocks cross-origin access to dev resources by default.** I
opened the app on `127.0.0.1:3010` while the dev server's origin is `localhost`,
so `/_next/*` was refused, the client bundle never ran, and nothing hydrated.
The only visible trace was one line in the *server* log:

```
⚠ Blocked cross-origin request to Next.js dev resource /_next/webpack-hmr from "127.0.0.1".
```

Fixed with `allowedDevOrigins: ["127.0.0.1", "localhost"]` in `next.config.ts`
(dev-only; no effect on production builds).

**The lesson is bigger than the bug.** All 33 API-level E2E checks passed while
the UI was completely non-functional — because they exercised the proxy, not the
browser. Only rendering the pages in a real browser caught it. That is why
`scripts/screenshot.mjs` is part of the test suite, not a nicety.

---

## 7. Supabase migration (29 Jul 2026)

Project `bom-review-assistant` / `qiwdjbsrtxeorzpcqmxy`, region `ap-southeast-1`
(nearest to Kulim), Postgres 17. All 11 tables created, RLS enabled with **no
policies** on every one — deliberate default-deny, since the backend connects
as the owner and enforces its own RBAC in `app/security.py`. `REVOKE ALL … FROM
anon, authenticated` on top, so a leaked publishable key reaches nothing. The
`rls_enabled_no_policy` advisor lints are therefore expected, not defects.

### F6 — the proxy DOES allow CONNECT to arbitrary ports ✅ better than feared

F1 established that raw TCP fails outright and everything must traverse
`proxy-png.intel.com:912`. Since libpq has no HTTP-proxy support, the working
assumption was that Postgres would be unreachable from a developer machine and
we would be stuck on PostgREST.

Measured instead:

| Path | Result |
|---|---|
| `db.<ref>.supabase.co:5432` raw TCP | `getaddrinfo failed` — Supabase now provisions direct connections IPv6-only |
| `aws-1-ap-southeast-1.pooler.supabase.com:5432` / `:6543` raw TCP | timeout |
| `<ref>.supabase.co:443` raw TCP | timeout (control — confirms F1 still holds) |
| **CONNECT to pooler `:5432` and `:6543` via the proxy** | **`HTTP/1.1 200 Connection established`** |
| PostgREST over HTTPS via the proxy | HTTP 200 from GoTrue, `cf-ray … -KUL` |

So the proxy tunnels *any* port, not just 443. `backend/scripts/proxy_tunnel.py`
terminates that locally: point `DATABASE_URL` at `127.0.0.1` and psycopg works
from this machine with full SQL. Verified end to end with a Postgres SSLRequest
through the tunnel — server answered `S`.

Also checked: **no corporate TLS interception.** Inside the CONNECT tunnel the
certificate is `subject=supabase.co`, `issuer=Google Trust Services WE1`, so the
proxy sees ciphertext only. Relevant to the PRD 5.1 sensitivity question.

`api.openai.com:443` also tunnels, so an external LLM endpoint is technically
reachable if governance ever permits one.

### F7 — two SQL-translation bugs the SQLite suite could not catch 🐛

`app/db.py` translates SQLite-flavoured SQL to Postgres. Both bugs below passed
the entire test suite, because on SQLite the translation is a no-op — they were
found by dumping the translated SQL and executing it against the live database.
`tests/test_sql_translation.py` now covers both.

1. **`datetime('now')` was never substituted.** It *contains* a string literal,
   so the literal-preserving pass split the token in half and the replacement
   never matched. Postgres has no `datetime()`; every write touching it would
   have failed. Fix: substitute the fixed token *before* the literal pass.

2. **Bare `user` was left unquoted** — the dangerous one. `audit_log` and
   `conversation_turn` have a column named `user`, which is reserved in
   Postgres, where bare `user` parses as `CURRENT_USER`. It does not error; it
   silently returns the wrong value. Measured on the live database:

   ```sql
   SELECT DISTINCT "user", user FROM audit_log;
   -- "user" -> 'alice'      (the column, correct)
   --  user  -> 'postgres'   (the database role, silently wrong)
   ```

   Every audit read would have attributed actions to the DB role. Fix: quote
   every bare occurrence, with lookarounds sparing `modified_user`, `user_id`,
   `users` and already-quoted `"user"`.

**Generalisable lesson:** a dual-dialect data layer whose tests only run against
the *default* dialect is untested on the other one. The parity test
(`test_schema_parity.py`) and the translation test exist for that reason, and
the translated SQL was executed against the real instance before being trusted.

### F8 — timestamps kept as TEXT on both dialects (deliberate)

`TIMESTAMPTZ` is better Postgres practice, but psycopg would then return
`datetime` objects where SQLite returns `str`, diverging every JSON response
between dev and production. Both sides therefore store TEXT formatted
`YYYY-MM-DD HH:MM:SS` UTC — Postgres via
`to_char((now() at time zone 'utc'), …)`. Verified byte-identical in format to
SQLite's `datetime('now')`. Worth converting once the port is proven, as one
change on its own.

### F9 — SQLite demoted to test-only (29 Jul 2026, owner decision)

The application now runs on Supabase Postgres and **refuses to start without
`DATABASE_URL`**. The failure this prevents is specific: a server that boots
fine, looks healthy, and records an engineer's approved changes into a local
file that no one else can see and no export job reads.

SQLite survives as the *test* backend only, on the owner's instruction to leave
the suite as it was. That keeps 66 tests offline and secret-free — which matters
here more than usual, since every connection must traverse the proxy and CI
would otherwise need the database password.

Two guards make the split hold:

- `tests/conftest.py` sets `BOM_ALLOW_SQLITE=1` **and clears `DATABASE_URL`**.
  Without the second half, a developer with `backend/.env` populated would have
  the suite silently run against the live Supabase project.
- `config.py` resolves the database target at **call time**, not import time.
  A module-level `DATABASE_URL` constant is captured before pytest's monkeypatch
  runs, so the clearing above would have had no effect. `db_path()` already
  worked this way; `database_url()` / `is_postgres()` / `allow_sqlite()` now
  match it.

`/health` reports `supabase-postgres` or `sqlite (test-only)` so it is never
ambiguous which backend a running process is on.

### F10 — the schema had no foreign keys at all 🐛

Discovered while mapping the relations: the constraint catalogue was **empty**.
Fourteen relations the code joins on, zero declared. The original SQLite
scaffold had none, and the Postgres port mirrored its shape for dialect parity —
so the omission travelled with it. Nothing stopped
`pending_change.confirmed_review_id` pointing at a review that did not exist, and
deleting a batch would have silently stranded its rows.

Eleven are now declared and enforced on both backends. Three stay soft
**deliberately**, and should not be "fixed" later:

| Link | Why it must not be a key |
|---|---|
| `recommendation_result.rule_version → rule_config` | An immutable stamp of the version active when the row was written. A FK invites an update; the audit trail depends on it not changing (PRD §10 determinism). |
| `bom_rows.machine_type ⊃ machine_criticality_config.pattern` | Substring match, not equality — one pattern covers many machines. Not expressible as a key. |
| `audit_log.entity + entity_id` | Polymorphic across six tables. One FK cannot express it, and splitting per target defeats having a single chronological trail. |

**`ON DELETE` is chosen per relation, not uniformly** — each choice encodes a
rule about the system:

- `CASCADE` on `bom_rows`, `recommendation_result`, `model_prediction_log` —
  derived data, reproducible by re-running ingest and score.
- `RESTRICT` on `review_history` and `pending_change` — the audit trail.
  Deleting a batch that carries decisions now **fails loudly** instead of
  shedding them.
- `SET NULL` on `conversation_turn.batch_id` and `item_note.origin_*` —
  provenance only. An `item_note` must outlive its batch; surviving roster
  rotation is the entire reason that table is keyed on `item_id`.

**Order of work mattered here.** The composite
`recommendation_result → bom_rows` link was the one at real risk: ingestion
writes `item_id` from the *stripped* record while the engine reads it back out of
the JSON payload, and suffixes duplicate keys as `item#dup<n>`. A whitespace
disagreement would have converted a silent mismatch into a failed batch run the
day the constraint landed. `scripts/check_referential_integrity.py` runs the real
Jan'26 workbook through ingest → score → review → chat and counts orphans for
every proposed relation; it reported zero across 2,768 scored rows before
anything was applied. Keep using it before adding constraints.

Delete semantics were then executed against the live database inside a
transaction and rolled back, rather than inferred from the DDL —
`review_history_batch_fk` correctly refused the batch delete. Seven regression
tests in `tests/test_foreign_keys.py` hold the same behaviour on SQLite, plus a
parity assertion that every constraint exists in *both* DDL blocks with the same
`ON DELETE` action — the F7 failure mode applied to constraints.

Postgres does not index the referencing side of a FK automatically, so eleven
supporting indexes went in with them; without those, every parent delete is a
sequential scan of the child.

**Applied out of band is not the same as declared in code.** The constraints and
those indexes were first executed straight against the live project, while
`init_db()` only ever ran `CREATE TABLE IF NOT EXISTS` — a no-op on tables that
already exist. Code and production were therefore two independent sources of
truth, and the suite could not see the gap because every test run builds a fresh
database. Both halves now live in `app/db.py`: `FOREIGN_KEYS` (the eleven
relations as data, named to match what is already on Supabase) plus
`FK_INDEX_DDL`, and `_ensure_foreign_keys()` adds anything missing from an
*existing* Postgres database on startup. Verified against the live project: all
eleven constraints and all eleven indexes already match, so the migration is a
no-op there and reproduces the same schema on a fresh one. SQLite has no
`ALTER TABLE ADD CONSTRAINT`, and needs none — it is the test backend and its
databases are always new. `tests/test_foreign_keys.py` now ties each
`ON DELETE` action to its own `REFERENCES <parent>` clause; the previous
substring check passed even when two actions on the same table were swapped.

### Node is no longer installed on this machine ⚠️

`frontend/node_modules` is present (465 MB) and `~/.npmrc` still carries the F1
proxy config, but there is no `node.exe` anywhere on the box. The frontend
changes in this round are therefore **unverified**: `npx tsc --noEmit`,
`npm run build`, `scripts/e2e_console.mjs` (33 checks) and
`scripts/screenshot.mjs` could not be run. Given F5 — where all 33 API checks
passed while the UI was entirely broken — the frontend should be treated as
unproven until Node is reinstalled and those four commands pass.

### F6 — the console needed endpoints the backend did not have

Added `GET /batches` and `GET /batches/{id}/summary` (workflow-state counts,
risk/action distribution, reason-code totals, exposure, export-ready count) plus
`backend/tests/test_console_endpoints.py`. Backend suite: **21/21 passing**.

## 7. What was tested

| Check | Result |
|---|---|
| `npx tsc --noEmit` | clean |
| `npm run build` | compiled 3.0s, 7 routes, no type errors |
| Backend suite (incl. new console tests) | **21/21 passing** |
| `scripts/e2e_console.mjs` — via the frontend proxy, real Jan'26 CSV | **33/33 passing** |
| `scripts/screenshot.mjs` — 5 pages × light/dark | 10/10 clean: no console errors, no horizontal overflow |
| Visual inspection | queue, item detail and config reviewed in both themes |

E2E highlights (all through `localhost:3010/api/backend/*`, not the backend
directly): upload 2,780 TCB rows in 1.4s → score 2,768 in 0.4s → queue sorted by
exposure (top item **$288,000**, High) → accept → self-approval refused (403) →
different senior approves (200) → invalid override refused (422) → export emits
**1 row** with **1,062 still pending** → chat answers with sources, refuses
without one.

## 8. Deliberate frontend decisions

| Decision | Why |
|---|---|
| BFF proxy rather than CORS | No CORS config; and it is the correct seam for Entra ID — the server injects identity, the client cannot pick its own role |
| Client components + `fetch` | Every view is role-dependent and mutation-heavy; server components would need per-role cache keys for no gain at this scale |
| Role switcher in the nav | Makes RBAC and the two-person rule *demonstrable* — the reviewer of the moment can be changed and the UI reacts |
| Ordinal single-hue ramp for the funnel | Stages are an order, not identities; validated light+dark |
| Icon + label on every status/risk chip | Colour never carries meaning alone |
| No chart library | Bars are `div`s against validated tokens — no CDN dependency, and the CSP stays trivial |

## 9. Frontend limitations

- **Dev-mode identity is spoofable by design.** The role switcher writes headers
  the backend trusts. Both ends must move to Entra ID together.
- **No automated visual regression.** `screenshot.mjs` captures and checks for
  errors/overflow; it does not diff against golden images.
- **No optimistic UI.** Every mutation refetches; correctness over snappiness at
  this stage.
- **The queue's `status` filter paginates in Python** (backend `recommend.py`),
  so a filtered page scans the batch. Fine at 2,768 rows, must move into SQL
  before multi-module scale.
- Screenshots were reviewed at 1440px. Layout is responsive and tables scroll in
  their own containers, but narrow-viewport rendering was not inspected.
