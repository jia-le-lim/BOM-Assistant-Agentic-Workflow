/**
 * E2E through the FRONTEND's own BFF proxy (port 3010), not the backend directly.
 * Proves the browser-facing contract: proxy forwarding, identity headers, RBAC
 * enforcement, the workflow states the console renders, and the export gate.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const BASE = "http://127.0.0.1:3010/api/backend";
const CSV = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../BOM table/BOM REVIEW_Jan'26 .csv",
);

const as = (user, role) => ({ "X-User": user, "X-Role": role });
const ENG = as("alice", "engineer");
const SENIOR = as("boss", "senior");
const VIEWER = as("eve", "viewer");
const ADMIN = as("root", "admin");

let pass = 0, fail = 0;
function check(name, cond, extra = "") {
  if (cond) { pass++; console.log(`  PASS  ${name}`); }
  else { fail++; console.log(`  FAIL  ${name} ${extra}`); }
}

async function api(p, { headers = {}, ...rest } = {}) {
  return fetch(`${BASE}/${p}`, { headers, ...rest });
}
const json = async (p, o) => (await api(p, o)).json();

async function waitUp() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await api("health", { headers: VIEWER });
      if (r.ok) return true;
    } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 1000));
  }
  return false;
}

console.log("=== E2E via Next.js BFF proxy (port 3010) ===\n");

if (!(await waitUp())) { console.log("Servers never came up"); process.exit(1); }
const health = await json("health", { headers: VIEWER });
check("proxy reaches backend /health", health.status === "ok", JSON.stringify(health));

// ---- RBAC through the proxy -------------------------------------------------
const forbidden = await api("upload-bom-file", { method: "POST", headers: VIEWER });
check("viewer blocked from upload (403)", forbidden.status === 403);

// ---- upload real Jan'26 -----------------------------------------------------
const fd = new FormData();
fd.append("file", new Blob([readFileSync(CSV)], { type: "text/csv" }), "BOM REVIEW_Jan26.csv");
fd.append("label", "Jan26-console");
fd.append("module_filter", "TCB");
let t = Date.now();
const up = await (await api("upload-bom-file", { method: "POST", headers: ENG, body: fd })).json();
const tUpload = ((Date.now() - t) / 1000).toFixed(1);
check("upload loaded 2780 TCB rows", up.rows_loaded === 2780, JSON.stringify(up));
check("12 rows quarantined", up.rows_quarantined === 12, JSON.stringify(up.quarantine_reasons));
const b = up.batch_id;

// ---- batch list (console landing page) --------------------------------------
const batches = await json("batches", { headers: VIEWER });
check("GET /batches returns the batch", batches[0]?.batch_id === b);

// ---- score ------------------------------------------------------------------
t = Date.now();
const run = await json(`run-recommendation?batch_id=${b}`, { method: "POST", headers: ENG });
const tScore = ((Date.now() - t) / 1000).toFixed(1);
check("scored 2768 rows", run.rows_scored === 2768, JSON.stringify(run.rows_scored));
check("rule_version stamped", run.rule_version === "0.2.0-tcb");

// ---- summary (dashboard data) ----------------------------------------------
const s = await json(`batches/${b}/summary`, { headers: VIEWER });
const stTotal = Object.values(s.statuses).reduce((a, x) => a + x, 0);
check("summary statuses cover every scored row", stTotal === 2768, `${stTotal}`);
check("pipeline has pending_review rows", s.statuses.pending_review > 0);
check("exposure computed", s.exposure_total_usd > 1_000_000, `$${s.exposure_total_usd}`);
check("nothing export-ready before review", s.export_ready_rows === 0);

// ---- triage queue, exposure-sorted -----------------------------------------
const q = await json(`recommendations?batch_id=${b}&status=pending_review&limit=5`, { headers: VIEWER });
check("queue paginates", q.items.length === 5 && q.total > 100, `total=${q.total}`);
const sorted = q.items.every((r, i, a) => i === 0 || a[i - 1].exposure_usd >= r.exposure_usd);
check("queue sorted by exposure desc", sorted);
const top = q.items[0];

// ---- filters ----------------------------------------------------------------
const highRisk = await json(`recommendations?batch_id=${b}&risk_level=High&limit=5`, { headers: VIEWER });
check("risk filter works", highRisk.items.every((r) => r.risk_level === "High"));
const trap = await json(
  `recommendations?batch_id=${b}&reason_code=ZERO_RECOMMENDATION_OVERRIDE&limit=5`, { headers: VIEWER });
check("reason-code filter finds the trap rows", trap.total > 0, `total=${trap.total}`);

// ---- item detail ------------------------------------------------------------
const detail = await json(`recommendations/${top.item_id}?batch_id=${b}`, { headers: VIEWER });
check("item detail has explanation", (detail.recommendation.explanation ?? "").length > 10);
check("item detail carries input context", detail.context.item_desc !== undefined);

// ---- review + two-person approval ------------------------------------------
const rev = await json(`review/${top.item_id}?batch_id=${b}`, {
  method: "POST", headers: { ...ENG, "Content-Type": "application/json" },
  body: JSON.stringify({ decision: "accept", comment: "console e2e" }),
});
check("accept recorded", rev.decision === "accept");
check("High risk demands senior approval", rev.requires_senior_approval === true);

const selfApprove = await api(`review/${top.item_id}/approve?batch_id=${b}`, {
  method: "POST", headers: as("alice", "senior"),
});
check("self-approval blocked (403)", selfApprove.status === 403);

const approved = await api(`review/${top.item_id}/approve?batch_id=${b}`, {
  method: "POST", headers: SENIOR,
});
check("different senior can approve", approved.status === 200);

// ---- override path ----------------------------------------------------------
const second = q.items[1];
const badOverride = await api(`review/${second.item_id}?batch_id=${b}`, {
  method: "POST", headers: { ...ENG, "Content-Type": "application/json" },
  body: JSON.stringify({ decision: "override", final_max: 1, final_rop: 5, final_min: 0 }),
});
check("override with max<rop rejected (422)", badOverride.status === 422);

const ok = await json(`review/${second.item_id}?batch_id=${b}`, {
  method: "POST", headers: { ...ENG, "Content-Type": "application/json" },
  body: JSON.stringify({ decision: "override", final_max: 9, final_rop: 4, final_min: 2,
                         justification: "Ad-hoc consumption/Bulk withdraw" }),
});
check("valid override recorded", ok.final_max === 9);

// ---- history (memory layer) -------------------------------------------------
const hist = await json(`history/${top.item_id}`, { headers: as("aud", "auditor") });
check("history readable by auditor", hist.reviews.length === 1);

// ---- config versioning ------------------------------------------------------
const cfgDenied = await api("config/rules", {
  method: "POST", headers: { ...ENG, "Content-Type": "application/json" },
  body: JSON.stringify({ rule_version: "x", updates: {} }),
});
check("non-admin cannot edit thresholds (403)", cfgDenied.status === 403);

const sameVersion = await api("config/rules", {
  method: "POST", headers: { ...ADMIN, "Content-Type": "application/json" },
  body: JSON.stringify({ rule_version: "0.2.0-tcb", updates: { long_lead_time_threshold: 30 } }),
});
check("version must change (400)", sameVersion.status === 400);

// ---- criticality two-person rule -------------------------------------------
await json("config/criticality", {
  method: "POST", headers: { ...ENG, "Content-Type": "application/json" },
  body: JSON.stringify({ pattern: "KnS TCX3", criticality: "High" }),
});
let cfg = await json("config/rules", { headers: VIEWER });
check("unconfirmed criticality invisible to engine",
      !("KnS TCX3" in (cfg.config.machine_criticality ?? {})));
await api("config/criticality/KnS%20TCX3/confirm", { method: "POST", headers: SENIOR });
cfg = await json("config/rules", { headers: VIEWER });
check("confirmed criticality active", cfg.config.machine_criticality?.["KnS TCX3"] === "High");

// ---- export gate ------------------------------------------------------------
const exp = await api(`export/wings?batch_id=${b}`, { headers: ENG });
const rows = (await exp.text()).trim().split("\n").length - 1;
const exported = exp.headers.get("x-rows-exported");
const pending = exp.headers.get("x-pending-review");
const awaiting = exp.headers.get("x-awaiting-senior");
check("export headers surface the gate", exported === "1" && Number(pending) > 1000,
      `exported=${exported} pending=${pending} awaiting=${awaiting}`);
check("export body matches header count", rows === Number(exported), `body=${rows}`);
check("override still awaiting senior, excluded", awaiting === "1");

// ---- chat guard -------------------------------------------------------------
const why = await json("chat", {
  method: "POST", headers: { ...VIEWER, "Content-Type": "application/json" },
  body: JSON.stringify({ question: `why item ${top.item_id}?` }),
});
check("chat explains from stored result", why.sources.length > 0);
const idk = await json("chat", {
  method: "POST", headers: { ...VIEWER, "Content-Type": "application/json" },
  body: JSON.stringify({ question: "forecast next quarter demand" }),
});
check("chat refuses without a source", idk.answer.includes("I don't know") && idk.sources.length === 0);

console.log(`\ntiming: upload ${tUpload}s | score ${tScore}s`);
console.log(`top queue item: ${top.item_id} $${Math.round(top.exposure_usd).toLocaleString()} ${top.risk_level}`);
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
