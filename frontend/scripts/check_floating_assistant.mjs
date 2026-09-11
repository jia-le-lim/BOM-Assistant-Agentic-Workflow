/** Real browser UI checks with isolated API fixtures. No live data is changed. */
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

const base = process.env.BASE_URL ?? "http://127.0.0.1:3010";
let browser;
try { browser = await chromium.launch(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}); }
catch (error) {
  if (process.platform !== "win32" || process.env.PLAYWRIGHT_CHANNEL) throw error;
  browser = await chromium.launch({ channel: "msedge" });
}
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
await context.addInitScript(() => localStorage.setItem("bom-session", JSON.stringify({ user: "root", role: "admin" })));
const page = await context.newPage();
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
page.on("console", (message) => {
  if (message.type() === "error" && !message.text().includes("Failed to load resource")) errors.push(message.text());
});
const requests = [], proposals = [];
let actions = [], holdResponse = null, rejectPart = null;
const config = { rule_version: "test-1", config: {
  long_lead_time_threshold: 60, zero_stock_risk_clt: 90, high_cost_threshold: 1500,
  low_cost_threshold: 100, recent_usage_days: 90, min_usage_months: 2,
  max_change_pct_review: 0.5, value_gate_usd: 2500, min_protective_stock: 1,
  autoclear_immaterial_usd: 0, autoclear_noop_abs: 0, autoclear_noop_rel: 0,
  autoclear_high_value_usd: 2500, autoclear_reliable: false,
  triage_guarded_assist_enabled: false, machine_criticality: {},
}};
await page.route("**/api/backend/**", async (route) => {
  const request = route.request();
  const path = new URL(request.url()).pathname.split("/api/backend/")[1];
  const reply = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  if (path === "chat" && request.method() === "POST") {
    const body = request.postDataJSON(), next = actions;
    actions = []; requests.push(body);
    if (holdResponse) await holdResponse;
    return reply({ answer: "Prepared a draft using your current page.", sources: [],
      batch_id: body.batch_id, session_id: "ui-test", turn_id: requests.length,
      intent: next.length ? "configure" : "lookup", staged_action: null, page_actions: next });
  }
  if (path === "health") return reply({ api_version: "test", database: "isolated fixture", llm_provider: "echo", llm_model: "UI test" });
  if (path === "chat/sessions") return reply({ sessions: [] });
  if (path === "batches") return reply([]);
  if (path === "pending-changes") return reply({ pending: [], count: 0 });
  if (request.method() === "POST" && path.startsWith("config/")) {
    const body = request.postDataJSON();
    if (body.match_key === rejectPart) return reply({ detail: "Fixture validation error" }, 422);
    proposals.push({ path, body });
    return reply({ rule_version: body.rule_version || "test-2", rule_id: proposals.length });
  }
  if (path === "config/rules") return reply(config);
  if (path === "config/part-categories" || path === "config/dormant-rules") return reply({ rules: [], confirmed: 0, pending: 0 });
  if (path === "recommendations") return reply({ items: [], total: 0, offset: 0, limit: 50 });
  if (path.startsWith("assist/")) return reply({ items: [], counts: {}, batch_id: 1 });
  return reply({ detail: "No fixture record" }, 404);
});

const panel = page.locator(".assistant-panel");
async function open() {
  if (!await page.locator(".assistant-float").evaluate((el) => el.open)) await page.locator(".assistant-float > summary").click();
}
async function ask(question) {
  await open();
  const previous = requests.length;
  await page.getByLabel("Ask NYRA", { exact: true }).fill(question);
  await panel.getByRole("button", { name: "Send", exact: true }).click();
  await page.waitForFunction(() => !document.querySelector(".assistant-thinking"));
  assert.equal(requests.length, previous + 1);
  return requests.at(-1);
}
const fill = (section, rows = [], updates = {}, rule_version = null) => ({ kind: "fill_settings",
  path: section === "dormant_rules" ? "/config/dormant" : "/config", section, rows, updates, rule_version });
const row = (match_key, policy = "hold_current", fixed_qty = null) => ({ scope: "item", match_key, policy, fixed_qty });
async function go(path) {
  await page.goto(base + path);
  await page.locator("main.shell-content").waitFor();
  if (path === "/config") await page.getByLabel("High cost ($)", { exact: true }).waitFor();
  if (path === "/config/dormant") await page.getByRole("heading", { name: "Dormant stocking rules", exact: true }).waitFor();
}

try {
  for (const [path, batch, item] of [["/", null, null], ["/batches/1", 1, null], ["/batches/1/items/ABC-123", 1, "ABC-123"]]) {
    await go(path);
    assert.equal(await page.locator(".assistant-float").count(), 1);
    const body = await ask("Where am I looking?");
    assert.equal(body.page_context.path, path);
    assert.equal(body.batch_id, batch);
    assert.equal(body.page_context.item_id, item);
    assert.equal(body.question, "Where am I looking?");
  }
  await go("/chat");
  assert.equal(await page.locator(".assistant-float").count(), 0, "Ask NYRA must not show a duplicate assistant");
  console.log("PASS: icon on app pages, hidden on Ask NYRA; current route and item sent verbatim");

  await go("/config");
  const criticality = page.locator('[data-assistant-section="Machine criticality"]');
  await criticality.scrollIntoViewIfNeeded();
  await criticality.getByLabel("Criticality", { exact: true }).selectOption("Medium");
  await criticality.getByLabel("Criticality", { exact: true }).focus();
  await criticality.locator("h2").evaluate((element) => {
    const range = document.createRange(); range.selectNodeContents(element);
    const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
  });
  const snapshot = (await ask("Explain this selected section")).page_context;
  assert.equal(snapshot.active_section, "Machine criticality");
  assert.equal(snapshot.focused_field.label, "Criticality");
  assert.equal(snapshot.focused_field.value, "Medium");
  assert.equal(snapshot.selected_text, "Machine criticality");
  assert.match(snapshot.visible_text, /Machine criticality/);
  assert.ok(!snapshot.visible_text.includes("High cost ($)"), "Offscreen threshold text must stay out of the viewport snapshot");
  console.log("PASS: viewport, section, selected text, and current focused field reach chat");

  await go("/config/dormant");
  actions = [fill("dormant_rules", [row("000111"), row("000222", "fixed_qty", 3), row("000333", "zero")])];
  await ask("hold current for 000111, keep 000222 at 3, zero 000333");
  const draft = page.getByLabel("dormant rules draft", { exact: true });
  assert.equal(await draft.locator("tbody tr").count(), 3);
  assert.equal(await draft.getByLabel("Item / category 1", { exact: true }).inputValue(), "000111");
  assert.equal(proposals.length, 0, "Filling must not submit");
  await page.keyboard.press("Escape");
  await draft.getByLabel("Quantity 2", { exact: true }).fill("4");
  rejectPart = "000222";
  await draft.getByRole("button", { name: "Propose 3 rules", exact: true }).click();
  await draft.getByRole("button", { name: "Propose 1 rule", exact: true }).waitFor();
  assert.equal(proposals.length, 2);
  assert.equal(await draft.getByLabel("Item / category 1", { exact: true }).inputValue(), "000222");
  rejectPart = null;
  await draft.getByRole("button", { name: "Propose 1 rule", exact: true }).click();
  await draft.getByText(/1 of 1 proposals submitted/).waitFor();
  assert.equal(proposals.length, 3, "Retry must not submit successful rows twice");
  assert.equal(proposals.at(-1).body.fixed_qty, 4);
  console.log("PASS: bulk draft is editable; submit uses corrected fields; failed rows retry independently");

  await go("/config");
  await page.getByLabel("Low cost ($)", { exact: true }).fill("123");
  actions = [fill("thresholds", [], { high_cost_threshold: 1800, autoclear_reliable: true }, "test-2"),
    fill("criticality", [{ pattern: "KnS TCX3", criticality: "High" }, { pattern: "ASM Phoenix", criticality: "Low" }])];
  await ask("Set high cost to 1800, enable reliable-stable, version test-2; set KnS TCX3 High and ASM Phoenix Low");
  assert.equal(await page.getByLabel("High cost ($)", { exact: true }).inputValue(), "1800");
  assert.equal(await page.getByLabel("Low cost ($)", { exact: true }).inputValue(), "123", "Unrelated drafts must survive");
  assert.equal(await page.getByLabel("New rule version (required)").inputValue(), "test-2");
  assert.equal(await page.getByLabel("Reliable-stable lever", { exact: true }).inputValue(), "true");
  assert.equal(await page.getByLabel("criticality draft", { exact: true }).locator("tbody tr").count(), 2);
  console.log("PASS: thresholds, toggles, version and criticality fill together while preserving unrelated edits");

  await go("/config/dormant");
  actions = [fill("dormant_rules", [row("000111")])];
  let release;
  holdResponse = new Promise((resolve) => { release = resolve; });
  const delayed = ask("hold current for 000111");
  await page.waitForFunction(() => !!document.querySelector(".assistant-thinking"));
  await page.getByLabel("Category", { exact: true }).fill("my-own-edit");
  release(); holdResponse = null; await delayed;
  assert.equal(await page.getByLabel("Category", { exact: true }).inputValue(), "my-own-edit");
  assert.match(await panel.innerText(), /kept your edits/);

  actions = [fill("dormant_rules", [row("000222")])];
  holdResponse = new Promise((resolve) => { release = resolve; });
  const moved = ask("hold current for 000222");
  await page.waitForFunction(() => !!document.querySelector(".assistant-thinking"));
  await page.locator('.rail a[href="/config"]').click();
  await page.getByLabel("High cost ($)", { exact: true }).waitFor();
  release(); holdResponse = null; await moved;
  assert.match(await panel.innerText(), /moved from Dormant stocking rules/);
  assert.equal(await page.getByLabel("High cost ($)", { exact: true }).inputValue(), "");
  const afterMove = await ask("What page am I on now?");
  assert.equal(afterMove.page_context.path, "/config");
  assert.equal(afterMove.batch_id, null);
  console.log("PASS: late responses cannot overwrite a changed form or follow the user onto another page");

  await page.locator('.rail a[href="/chat"]').click();
  await page.waitForURL("**/chat");
  assert.equal(await page.locator(".assistant-float").count(), 0);
  await page.locator('.rail a[href="/config/dormant"]').click();
  await page.waitForURL("**/config/dormant");
  await open();
  assert.ok(await panel.locator(".assistant-turn").count() > 0, "Floating conversation survives visiting full chat");
  console.log("PASS: no duplicate on full chat; floating conversation survives navigation");

  await page.setViewportSize({ width: 390, height: 844 });
  await open();
  const box = await panel.boundingBox();
  assert.ok(box.x >= 0 && box.x + box.width <= 390 && box.y >= 0 && box.y + box.height <= 844);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
  await page.keyboard.press("Escape");
  assert.equal(await page.locator(".assistant-float").evaluate((el) => el.open), false);
  console.log("PASS: mobile panel stays in viewport and Escape closes it");

  await page.setViewportSize({ width: 1440, height: 1000 });
  await go("/config/dormant");
  actions = [fill("dormant_rules", [row("000111"), row("000222", "fixed_qty", 4)])];
  await ask("Hold current for 000111; keep 000222 at 4");
  mkdirSync(".assistant-check", { recursive: true });
  await page.screenshot({ path: ".assistant-check/dormant-rules.png", fullPage: true });
  assert.deepEqual(errors, [], "Browser errors: " + errors.join("; "));
  console.log("All floating assistant browser checks passed.");
} finally { await browser.close(); }
