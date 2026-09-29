/** Browser checks against isolated fixtures; never writes to the live backend. */
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { chromium } from "playwright";

const base = process.env.BASE_URL ?? "http://localhost:3011";
let browser;
try { browser = await chromium.launch(); }
catch { browser = await chromium.launch({ channel: "msedge" }); }
const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
await context.addInitScript(() => localStorage.setItem("bom-session", JSON.stringify({ user: "alice", role: "engineer" })));
await context.addInitScript(() => {
  window.__reviewMotion = [];
  const start = document.startViewTransition?.bind(document);
  if (!start) return;
  document.startViewTransition = (update) => {
    const transition = start(update);
    transition.ready.then(() => {
      window.__reviewMotion.push({ kind: document.documentElement.dataset.reviewTransition,
        animations: document.getAnimations().map((animation) => animation.animationName).filter(Boolean),
        geometry: document.getAnimations().filter((animation) => animation.animationName?.includes("review-morph"))
          .map((animation) => ({ name: animation.animationName, duration: animation.effect.getTiming().duration,
            frames: animation.effect.getKeyframes().map(({ width, height, transform }) => ({ width, height, transform })) })) });
      if (window.__pauseMorph) {
        document.getAnimations().forEach((animation) => { animation.pause(); animation.currentTime = 180; });
        window.__morphPaused = true;
      }
    }).catch(() => {});
    return transition;
  };
});
const page = await context.newPage();
const errors = [], writes = [], chats = [];
page.on("pageerror", (error) => errors.push(error.message));
const rows = Array.from({ length: 27 }, (_, i) => ({
  batch_id: 1, item_id: i < 2 ? "PART-001" : `PART-${String(i).padStart(3, "0")}`,
  stockroom_id: i === 1 ? "ROOM B" : "ROOM A", item_desc: i === 0
    ? "Precision pressure regulator with an unusually long description for truncation"
    : i === 1 ? "Pressure regulator in the second stockroom" : `Replacement assembly ${i}`,
  part_category: "Mechanical", new_max: 10 + i, new_rop: 5 + i, new_min: 2,
  current_max: 20 + i, current_rop: 10 + i, review_required: "Y", action: "Decrease",
  reason_code: "MAX_CHANGE", risk_level: "Medium", confidence: .9,
  explanation: `Demand supports ${10 + i} units in ${i === 1 ? "ROOM B" : "ROOM A"}.`,
  exposure_usd: 20000 - i * 100, model_version: "test", rule_version: "test",
  scored_at: "2026-09-11", status: "pending_review", route: "active",
  consumable: "constant", agreement: "diverge", agreement_source: "factory",
}));
const assist = rows.map((row) => ({ ...row, verdict: "flag_for_review", reasons: ["PRIOR_OVERRIDE"],
  narrative: "Prior decisions suggest keeping a smaller protective stock.", suggested_max: 8,
  suggested_rop: 4, suggestion_basis: "prior_accepted", assisted_at: "2026-09-11" }));
let delayRoomB = false, failDetail = false, failHistory = false, sharedWorkspace = false;
await page.route("**/api/pilot-session", (route) => route.fulfill({ json: { required: false, user: "pilot", role: "admin" } }));
await page.route("**/api/backend/**", async (route) => {
  const request = route.request(), url = new URL(request.url());
  const path = url.pathname.split("/api/backend/")[1];
  const reply = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  if (path === "health") return reply({ api_version: "test", database: "fixtures", llm_provider: "echo", llm_model: "test" });
  if (path === "batches") return reply([]);
  if (path === "pending-changes") return reply({ pending: [], count: 0 });
  if (path === "config/rules") return reply({ rule_version: "test", config: { triage_guarded_assist_enabled: false } });
  if (path === "batches/1/summary") return reply({
    read_only: sharedWorkspace,
    batch: { batch_id: 1, label: "Review layout check", status: "scored", row_count: 27, quarantined_count: 0, scored_rule_version: "test" },
    scored: 27, statuses: { pending_review: rows.filter((r) => r.status === "pending_review").length },
    risk_levels: { Medium: 27 }, actions: { Decrease: 27 }, consumables: { constant: 27 },
    routes: {}, agreements: {}, reason_codes: {}, exposure_total_usd: 500000,
    exposure_pending_usd: 500000, bulk_acceptable: 0, pareto: { items_for_80pct: 20, top100_coverage_pct: 100 }, export_ready_rows: 0,
  });
  if (path === "recommendations") {
    const status = url.searchParams.get("status"), q = url.searchParams.get("q")?.toLowerCase();
    const filtered = rows.filter((r) => (!status || r.status === status) && (!q || `${r.item_id} ${r.stockroom_id}`.toLowerCase().includes(q)));
    const offset = Number(url.searchParams.get("offset") || 0);
    return reply({ items: filtered.slice(offset, offset + 25), total: filtered.length, offset, limit: 25 });
  }
  if (path.startsWith("recommendations/")) {
    if (failDetail) return reply({ detail: "Detail unavailable" }, 503);
    const room = url.searchParams.get("stockroom_id");
    assert.ok(room, "Detail must specify its stockroom");
    if (delayRoomB && room === "ROOM B") await new Promise((resolve) => setTimeout(resolve, 900));
    const row = rows.find((r) => r.item_id === decodeURIComponent(path.split("/")[1]) && r.stockroom_id === room);
    return reply({ recommendation: row, status: row.status, latest_review: null, read_only: sharedWorkspace,
      context: { item_desc: row.item_desc, max_qty: row.current_max, rop_qty: row.current_rop, min_qty: 1 } });
  }
  if (path.startsWith("history/")) return failHistory ? reply({ detail: "History unavailable" }, 503) : reply({ reviews: [] });
  if (path === "review/justification-templates") return reply({ templates: [{ justification: "Demand change", definition: "Demand has changed." }] });
  if (path.startsWith("similarity/")) return reply({ detail: "No peers" }, 404);
  if (path === "assist/1") return reply({ batch_id: 1, items: assist.filter((a) =>
    (!url.searchParams.get("item_id") || a.item_id === url.searchParams.get("item_id")) &&
    (!url.searchParams.get("stockroom_id") || a.stockroom_id === url.searchParams.get("stockroom_id"))),
    counts: { flag_for_review: 27, bulk_accept_candidate: 0, needs_context: 0 }, model_version: "test" });
  if (path.startsWith("review/") && request.method() === "POST") {
    const body = request.postDataJSON(), room = url.searchParams.get("stockroom_id");
    writes.push({ path, room, body });
    const row = rows.find((r) => r.item_id === decodeURIComponent(path.split("/")[1]) && r.stockroom_id === room);
    assert.ok(row, "Review must target one exact stockroom");
    row.status = body.decision === "override" ? "awaiting_senior" : "reviewed";
    return reply({ status: row.status, requires_senior_approval: body.decision === "override" });
  }
  if (path === "chat") {
    chats.push(request.postDataJSON());
    return reply({ answer: "Fixture answer", sources: [], page_actions: [], batch_id: 1, session_id: "check", intent: "lookup", staged_action: null });
  }
  return reply({ detail: `No fixture: ${path}` }, 404);
});

const panel = page.locator(".review-detail");
const itemButtons = page.locator(".review-item-button");
async function ready() {
  await page.waitForFunction(() => !document.documentElement.dataset.reviewTransition);
  await panel.getByRole("button", { name: "Record decision", exact: true }).waitFor();
  await page.waitForFunction(() => !document.querySelector('.review-detail button.btn-primary:disabled') && !document.querySelector('.review-detail [aria-busy="true"]'));
}
try {
  await page.goto(base + "/batches/1");
  await page.locator("table tbody tr").first().waitFor();
  assert.equal(await page.getByRole("button", { name: "Rows", exact: true }).getAttribute("aria-pressed"), "true");
  mkdirSync(".assistant-check", { recursive: true });
  await page.screenshot({ path: ".assistant-check/review-morph-before.png" });
  await page.evaluate(() => { window.__pauseMorph = true; });
  await page.getByRole("button", { name: "Split view", exact: true }).click();
  await page.waitForFunction(() => window.__morphPaused);
  await page.screenshot({ path: ".assistant-check/review-morph-midpoint.png" });
  writeFileSync(".assistant-check/review-morph-geometry.json", JSON.stringify(await page.evaluate(() => window.__reviewMotion), null, 2));
  await page.evaluate(() => {
    window.__pauseMorph = false;
    document.getAnimations().forEach((animation) => animation.play());
  });
  await ready();
  assert.equal(await itemButtons.count(), 25);
  assert.match(await panel.innerText(), /20 \/ 10/);
  assert.match(await panel.innerText(), /10 \/ 5/);
  const queueUrl = page.url();
  delayRoomB = true;
  await itemButtons.nth(1).click();
  await page.waitForFunction(() => document.querySelector(".review-detail")?.dataset.assistantStockroomId === "ROOM B");
  assert.match(await panel.innerText(), /21 \/ 11/);
  await itemButtons.nth(0).click();
  await ready();
  await page.waitForTimeout(1100);
  assert.match(await panel.locator(".review-detail-header").innerText(), /ROOM A/);
  assert.doesNotMatch(await panel.locator(".item-review-heading").innerText(), /second stockroom/);
  assert.equal(page.url(), queueUrl, "Item selection must not navigate");
  delayRoomB = false;

  await panel.getByRole("button", { name: "Override with my values", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('input[aria-label="Select PART-001 in ROOM A"]')?.disabled);
  assert.equal(await page.getByLabel("Select PART-001 in ROOM A", { exact: true }).isDisabled(), true,
    "A draft override must not be bulk accepted with engine values");
  await panel.getByLabel("Max", { exact: true }).fill("16");
  await panel.getByLabel("Comment", { exact: true }).fill("Keep this item draft");
  await itemButtons.nth(1).click();
  await ready();
  assert.equal(await panel.getByLabel("Comment", { exact: true }).inputValue(), "");
  await itemButtons.nth(0).click();
  await ready();
  assert.equal(await panel.getByLabel("Max", { exact: true }).inputValue(), "16");
  assert.equal(await panel.getByLabel("Comment", { exact: true }).inputValue(), "Keep this item draft");
  await page.getByRole("button", { name: "Rows", exact: true }).click();
  await page.getByRole("button", { name: "Split view", exact: true }).click();
  await ready();
  assert.equal(await panel.getByLabel("Comment", { exact: true }).inputValue(), "Keep this item draft");

  await itemButtons.nth(1).click();
  await ready();
  await page.locator(".assistant-float > summary").click();
  await page.getByRole("textbox", { name: "Ask NYRA", exact: true }).fill("Explain this item");
  await page.locator(".assistant-panel").getByRole("button", { name: "Send", exact: true }).click();
  await page.waitForFunction(() => !document.querySelector(".assistant-thinking"));
  assert.equal(chats.at(-1).page_context.item_id, "PART-001");
  assert.equal(chats.at(-1).page_context.stockroom_id, "ROOM B");
  await page.locator(".assistant-float > summary").click();
  await panel.getByRole("button", { name: "Use these numbers", exact: true }).click();
  assert.equal(await panel.getByLabel("Max", { exact: true }).inputValue(), "8");
  await panel.getByRole("button", { name: "Record decision", exact: true }).click();
  await page.getByText("Item decision saved. The queue has been updated.", { exact: true }).waitFor();
  await ready();
  assert.equal(writes.at(-1).room, "ROOM B");
  assert.equal(writes.at(-1).body.decision, "override");
  assert.equal(writes.at(-1).body.final_max, 8);
  assert.equal(await itemButtons.filter({ hasText: "ROOM B" }).count(), 0);

  const motion = await page.evaluate(() => window.__reviewMotion);
  assert.ok(motion.some((entry) => entry.kind === "layout" && entry.animations.includes("review-panel-open")), "Detail panel must expand into place");
  assert.ok(motion.some((entry) => entry.kind === "layout" && entry.geometry.some((track) =>
    track.duration === 560 && track.frames.length > 1 && track.frames[0].width !== track.frames.at(-1).width)),
  "Shared rows must interpolate their width across layouts");
  assert.ok(motion.some((entry) => entry.kind === "layout" && entry.geometry.some((track) =>
    track.frames.length > 1 && track.frames[0].transform !== track.frames.at(-1).transform)),
  "Shared items must move between their measured positions");
  for (const direction of ["contract", "expand"]) {
    assert.ok(motion.some((entry) => entry.kind === "layout" && entry.geometry.some((track) => {
      const start = parseFloat(track.frames[0]?.width), end = parseFloat(track.frames.at(-1)?.width);
      return direction === "contract" ? start > end * 2 : end > start * 2;
    })), `Rows must ${direction} when switching layouts`);
  }
  assert.ok(motion.some((entry) => entry.kind === "item" && entry.animations.includes("review-item-reveal")), "Item transition must run");
  // Back-to-back clicks must leave the final selected item active.
  await itemButtons.first().evaluate((button) => {
    const buttons = button.closest(".review-list-scroll").querySelectorAll(".review-item-button");
    buttons[2].click(); buttons[3].click();
  });
  await ready();
  assert.equal(await itemButtons.nth(3).getAttribute("aria-current"), "true");
  await itemButtons.nth(3).evaluate((button) => {
    const buttons = button.closest(".review-list-scroll").querySelectorAll(".review-item-button");
    buttons[1].click(); button.click();
  });
  await ready();
  assert.equal(await itemButtons.nth(3).getAttribute("aria-current"), "true", "Reversing a pending selection keeps the original item");
  await page.locator(".review-view-toggle").evaluate((toggle) => {
    const buttons = toggle.querySelectorAll("button");
    buttons[0].click(); buttons[1].click();
  });
  await ready();
  assert.equal(await page.getByRole("button", { name: "Split view", exact: true }).getAttribute("aria-pressed"), "true");
  await page.emulateMedia({ reducedMotion: "reduce" });
  const beforeReduced = await page.evaluate(() => window.__reviewMotion.length);
  await itemButtons.first().click();
  await ready();
  assert.equal(await page.evaluate(() => window.__reviewMotion.length), beforeReduced);
  assert.equal(await panel.locator(".item-review-embedded").evaluate((element) => getComputedStyle(element).animationName), "none");
  await page.emulateMedia({ reducedMotion: "no-preference" });
  // A browser without snapshot transitions must still switch items and layouts.
  await page.evaluate(() => { document.startViewTransition = undefined; });
  await itemButtons.nth(2).click();
  await ready();
  assert.equal(await itemButtons.nth(2).getAttribute("aria-current"), "true");
  await page.getByRole("button", { name: "Rows", exact: true }).click();
  await page.getByRole("button", { name: "Split view", exact: true }).click();
  await ready();

  await page.reload();
  await ready();
  assert.equal(await page.getByRole("button", { name: "Split view", exact: true }).getAttribute("aria-pressed"), "true");
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await page.waitForFunction(() => document.querySelectorAll(".review-item-button").length === 1);
  await ready();
  await page.getByRole("button", { name: "Previous", exact: true }).click();
  await page.waitForFunction(() => document.querySelectorAll(".review-item-button").length === 25);
  const search = page.getByLabel("Search the review queue by item or stockroom");
  await search.fill("NO-MATCH");
  await page.getByText("No rows match these filters.", { exact: false }).waitFor();
  assert.equal(await panel.count(), 0);
  await search.fill("");
  await ready();

  failDetail = true;
  await itemButtons.nth(2).click();
  await panel.getByText("Detail unavailable", { exact: true }).waitFor();
  failDetail = false;
  failHistory = true;
  await panel.getByRole("button", { name: "Retry details", exact: true }).click();
  await panel.getByRole("heading", { name: "Engine recommendation", exact: true }).waitFor();
  await panel.getByText(/Some supporting details could not load/).waitFor();
  failHistory = false;
  await panel.getByRole("button", { name: "Retry details", exact: true }).click();
  await ready();

  mkdirSync(".assistant-check", { recursive: true });
  await page.locator(".review-split").scrollIntoViewIfNeeded();
  await page.screenshot({ path: ".assistant-check/review-split-desktop.png" });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.screenshot({ path: ".assistant-check/review-split-dark.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  await panel.locator(".review-detail-header").scrollIntoViewIfNeeded();
  await page.screenshot({ path: ".assistant-check/review-split-mobile.png" });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "Mobile must not overflow horizontally");
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.getByRole("button", { name: "Rows", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.review-view-toggle button[aria-pressed="true"]')?.textContent.trim() === "Rows");
  await page.reload();
  await page.locator("table tbody tr").first().waitFor();
  assert.equal(await page.getByRole("button", { name: "Rows", exact: true }).getAttribute("aria-pressed"), "true");
  const href = await page.locator("table tbody tr a").first().getAttribute("href");
  assert.match(href, /stockroom_id=ROOM%20A/);
  await page.goto(base + href);
  await page.getByRole("heading", { name: "Engine recommendation", exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Back to items", exact: false }).count(), 1);
  await page.goto(base + "/batches/1");
  await page.getByRole("button", { name: "Split view", exact: true }).click();
  await ready();
  assert.equal(await page.locator(".side-user select").count(), 0);
  assert.equal(await page.locator(".side-access").textContent(), "Administrator");
  assert.equal(await panel.getByRole("button", { name: "Record decision", exact: true }).count(), 1);
  assert.ok(await page.locator(".review-list-row input[type=checkbox]").count() > 0);
  sharedWorkspace = true;
  const previousWrites = writes.length;
  await page.reload();
  await page.getByText("Viewing another user's workspace. You have read-only access.").waitFor();
  await panel.getByText("You have read-only access to this user's workspace.").waitFor();
  assert.equal(await panel.getByRole("button", { name: "Record decision", exact: true }).count(), 0);
  assert.equal(await page.getByRole("button", { name: /re-run engine/i }).isDisabled(), true);
  assert.equal(writes.length, previousWrites, "Viewing shared rows must not record decisions");
  assert.deepEqual(errors, []);
  console.log("PASS: animated layouts and items, rapid switching, reduced motion, animation fallback, layout persistence, stockrooms, stale responses, drafts, bulk safeguards, assistant context, review refresh, pagination, search, errors, mobile, standalone detail and Administrator access.");
} finally { await browser.close(); }
