/** Settings interactions and responsive rendering against isolated API fixtures. */
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

const base = process.env.BASE_URL ?? "http://localhost:3011";
let browser;
try { browser = await chromium.launch(); }
catch (error) {
  if (process.platform !== "win32") throw error;
  browser = await chromium.launch({ channel: "msedge" });
}
const context = await browser.newContext({ viewport: { width: 1440, height: 1080 }, colorScheme: "dark" });
const page = await context.newPage();
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
const writes = [];
let failSave = false;
const config = { rule_version: "test-1", config: {
  long_lead_time_threshold: 60, zero_stock_risk_clt: 90, high_cost_threshold: 1500,
  low_cost_threshold: 100, recent_usage_days: 90, min_usage_months: 2,
  max_change_pct_review: 0.5, value_gate_usd: 2500, min_protective_stock: 1,
  autoclear_immaterial_usd: 0, autoclear_noop_abs: 0, autoclear_noop_rel: 0,
  autoclear_high_value_usd: 2500, autoclear_reliable: false,
  triage_guarded_assist_enabled: false, machine_criticality: { "KnS TCX3": "High" },
} };
await page.route("**/api/pilot-session", (route) => route.fulfill({ json: { required: false, user: "ui-check" } }));
await page.route("**/api/backend/**", (route) => {
  const request = route.request();
  const path = new URL(request.url()).pathname.split("/api/backend/")[1];
  const reply = (json, status = 200) => route.fulfill({ json, status });
  if (request.method() === "POST") {
    const body = request.postDataJSON();
    writes.push({ path, body });
    if (path === "config/rules") {
      if (failSave) return reply({ detail: "This version already exists. Choose a new version." }, 409);
      config.rule_version = body.rule_version;
      Object.assign(config.config, body.updates);
      return reply({ rule_version: config.rule_version });
    }
    return reply({ rule_id: writes.length });
  }
  if (path === "config/rules") return reply(config);
  if (path === "config/part-categories") return reply({ confirmed: 1, pending: 1, rules: [
    { pattern: "SENSOR", category: "sensor", priority: 100, confirmed: true, confirmed_by: "senior" },
    { pattern: "BRACKET", category: "bracket", priority: 500, confirmed: false, set_by: "engineer" },
  ] });
  if (path === "config/dormant-rules") return reply({ confirmed: 0, pending: 0, rules: [] });
  if (path === "config/dormant-rules/coverage") return reply({ matched: 5, dormant_rows: 10, pct: 50,
    confirmed_rules: 1, proposed_book_usd: 200, engine_book_usd: 0, delta_usd: 200 });
  if (path === "health") return reply({ api_version: "test", database: "fixture", llm_provider: "test", llm_model: "NYRA" });
  if (path === "chat/sessions") return reply({ sessions: [] });
  return reply({});
});

const nav = page.getByRole("navigation", { name: "Settings sections" });
const field = (name) => page.getByLabel(name, { exact: true });
const save = page.getByRole("button", { name: "Save changes", exact: true });
async function choose(name) { await nav.getByRole("button", { name, exact: true }).click(); }
async function noOverflow() {
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
}
mkdirSync(".local/settings", { recursive: true });

try {
  await page.goto(`${base}/config`);
  await field("High cost ($)").waitFor();
  assert.equal(await field("High cost ($)").inputValue(), "1500");
  assert.equal(await save.isDisabled(), true);
  await field("High cost ($)").fill("1800");
  await field("Max change before review").fill("0.75");
  await field("New rule version (required)").fill("test-2");
  assert.equal(await page.locator(".settings-changed:visible").count(), 3);
  await page.screenshot({ path: ".local/settings/dark-thresholds.png", fullPage: true });

  await choose("Auto-clear policy");
  await field("Reliable-stable lever").selectOption("true");
  await choose("Review assist");
  await field("Guarded bulk acceptance").selectOption("true");
  await choose("Thresholds");
  assert.equal(await field("High cost ($)").inputValue(), "1800", "Section navigation preserves drafts");
  await save.click();
  await page.getByText(/Saved as test-2/).waitFor();
  assert.deepEqual(writes[0], { path: "config/rules", body: { rule_version: "test-2", updates: {
    high_cost_threshold: 1800, max_change_pct_review: 0.75, autoclear_reliable: true, triage_guarded_assist_enabled: true,
  } } });
  assert.equal(await page.locator(".settings-changed:visible").count(), 0);
  console.log("PASS: current values, changed badges, section persistence, and versioned save across rule sections");

  failSave = true;
  await field("High cost ($)").fill("1900");
  await field("New rule version (required)").fill("duplicate-version");
  await save.click();
  await page.getByText("This version already exists. Choose a new version.").waitFor();
  assert.equal(await field("High cost ($)").inputValue(), "1900");
  await field("High cost ($)").fill("");
  assert.equal(await save.isDisabled(), true, "Blank numeric edits cannot be saved");
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  assert.equal(await field("High cost ($)").inputValue(), "1800");
  assert.equal(await field("New rule version (required)").inputValue(), "");
  assert.equal(await save.isDisabled(), true);
  console.log("PASS: failed saves preserve edits; Cancel restores saved values; empty values block saving");

  await choose("Machine criticality");
  await field("Machine type contains").fill("ASM Phoenix");
  await field("Criticality").selectOption("Medium");
  await choose("Part categories");
  await field("Pattern (regex on description)").fill("GRIPPER");
  await field("Category").fill("gripper");
  await field("Priority").fill("250");
  await page.getByRole("button", { name: "Propose", exact: true }).click();
  await page.getByText(/Proposed "GRIPPER"/).waitFor();
  assert.deepEqual(writes.at(-1).body, { pattern: "GRIPPER", category: "gripper", priority: 250 });
  await choose("Machine criticality");
  assert.equal(await field("Machine type contains").inputValue(), "ASM Phoenix");
  await page.getByRole("button", { name: "Propose", exact: true }).click();
  await page.getByText(/Proposed "ASM Phoenix"/).waitFor();
  assert.deepEqual(writes.at(-1).body, { pattern: "ASM Phoenix", criticality: "Medium" });
  console.log("PASS: category and criticality proposals retain their API payloads and independent drafts");

  for (const theme of ["dark", "light"]) {
    await page.emulateMedia({ colorScheme: theme });
    for (const width of [1440, 1159, 768, 390]) {
      await page.setViewportSize({ width, height: 900 });
      for (const name of ["Thresholds", "Auto-clear policy", "Review assist", "Machine criticality", "Part categories"]) {
        await choose(name);
        await noOverflow();
      }
      await choose("Auto-clear policy");
      await page.screenshot({ path: `.local/settings/${theme}-${width}.png`, fullPage: true });
    }
  }
  console.log("PASS: all sections fit desktop, tablet, and mobile in both themes");

  await nav.getByRole("link", { name: "Dormant rules", exact: true }).click();
  await field("Scope").waitFor();
  await field("Scope").selectOption("item");
  await field("Item id").fill("000123");
  await field("Policy").selectOption("fixed_qty");
  await field("Quantity").fill("4");
  await page.getByRole("button", { name: "Propose", exact: true }).click();
  await page.getByText(/Proposed for your account/).waitFor();
  assert.deepEqual(writes.at(-1).body, { scope: "item", match_key: "000123", policy: "fixed_qty", fixed_qty: 4 });
  await noOverflow();
  await nav.getByRole("link", { name: "Auto-clear policy", exact: true }).click();
  await page.getByRole("heading", { name: "Auto-clear policy", exact: true }).waitFor();
  assert.equal(await nav.getByRole("button", { name: "Auto-clear policy", exact: true }).getAttribute("aria-current"), "page");
  assert.deepEqual(errors, []);
  console.log("PASS: dormant proposals, settings deep links, and no browser runtime errors");
} finally { await browser.close(); }
