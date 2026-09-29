/** Workspace setup and upload flow, with isolated API fixtures. */
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

const base = process.env.BASE_URL ?? "http://localhost:3011";
let browser;
try { browser = await chromium.launch(); }
catch (error) { if (process.platform !== "win32") throw error; browser = await chromium.launch({ channel: "msedge" }); }
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: "dark", reducedMotion: "reduce" });
const page = await context.newPage();
page.setDefaultTimeout(15000);
const errors = [], batches = [], uploads = [], scores = [];
let failCreate = false, failUpload = false, failScore = false, failLoad = false;
page.on("pageerror", (error) => errors.push(error.message));
await page.route("**/api/pilot-session", (route) => route.fulfill({ json: { required: false, user: "pilot", role: "admin" } }));
await page.route("**/api/backend/**", (route) => {
  const request = route.request(), url = new URL(request.url());
  const path = url.pathname.split("/api/backend/")[1];
  const reply = (json, status = 200) => route.fulfill({ status, json });
  if (path === "health") return reply({ api_version: "test", database: "fixture", llm_provider: "test", llm_model: "NYRA" });
  if (path === "chat/sessions") return reply({ sessions: [] });
  if (path === "batches" && request.method() === "POST") {
    if (failCreate) return reply({ detail: "Could not create workspace. Please retry." }, 503);
    const body = request.postDataJSON();
    const batch = { batch_id: batches.length + 1, ...body, status: "draft", source_filename: null,
      uploaded_by: "pilot", uploaded_at: "2026-09-15", row_count: 0, quarantined_count: 0, scored_rule_version: null, scored_at: null };
    batches.unshift(batch); return reply(batch, 201);
  }
  if (path === "batches") return failLoad ? reply({ detail: "Connection interrupted" }, 503) : reply(batches);
  if (/^batches\/\d+\/summary$/.test(path)) {
    const batch = batches.find((batch) => batch.batch_id === Number(path.split("/")[1]));
    return batch ? reply({ batch }) : reply({ detail: "Workspace not found" }, 404);
  }
  if (path === "upload-bom-file") {
    if (failUpload) return reply({ detail: "Missing required columns: item_id" }, 400);
    const data = request.postDataBuffer().toString(); uploads.push(data);
    const id = Number(data.match(/name="batch_id"\r\n\r\n(\d+)/)?.[1]);
    const batch = batches.find((batch) => batch.batch_id === id);
    assert.ok(batch, "Upload must target an existing workspace");
    Object.assign(batch, { status: "loaded", source_filename: "review.csv", row_count: 12, quarantined_count: 1 });
    return reply({ batch_id: id, label: batch.label, module_filter: batch.module_filter, rows_loaded: 12, rows_quarantined: 1, quarantine_reasons: { NEGATIVE_CONSUMPTION: 1 } });
  }
  if (path === "run-recommendation") {
    scores.push(Number(url.searchParams.get("batch_id")));
    if (failScore) return reply({ detail: "Engine unavailable. Try again." }, 503);
    batches.find((batch) => batch.batch_id === scores.at(-1)).status = "scored";
    return reply({ rows_scored: 11 });
  }
  return reply({});
});
mkdirSync(".local/workspaces", { recursive: true });
const screenshot = async (name) => {
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  return page.screenshot({ path: `.local/workspaces/${name}.png`, fullPage: true });
};
const noOverflow = async () => assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, "No horizontal page overflow");
const createModal = () => page.getByRole("dialog", { name: "Create a new workspace" });
async function create(name) {
  await page.getByRole("button", { name: "Create workspace", exact: true }).click();
  await createModal().getByLabel("Workspace name").fill(name);
  await createModal().getByRole("button", { name: "Create workspace", exact: true }).click();
  await page.getByRole("heading", { name: "Your workspace is ready", exact: true }).waitFor();
}

try {
  await page.goto(base);
  await page.getByRole("heading", { name: "Your next BOM review starts here" }).waitFor();
  assert.equal(await page.getByLabel("BOM datasheet").count(), 0, "Create workspace before selecting a file");
  await screenshot("empty-dark");
  await page.emulateMedia({ colorScheme: "light" }); await screenshot("empty-light");
  await page.emulateMedia({ colorScheme: "dark" });
  await page.setViewportSize({ width: 390, height: 844 }); await noOverflow(); await screenshot("empty-mobile");
  await page.setViewportSize({ width: 1440, height: 1000 });

  await page.getByRole("button", { name: "Read the setup guide" }).click();
  await page.getByRole("dialog").getByText("item_id", { exact: true }).waitFor();
  await page.keyboard.press("Escape");
  assert.equal(await page.getByRole("button", { name: "Read the setup guide" }).evaluate((el) => el === document.activeElement), true);
  await page.locator(".rail").getByRole("link", { name: "New workspace", exact: true }).click();
  await createModal().waitFor();
  assert.equal(await createModal().getByRole("button", { name: "Create workspace", exact: true }).isDisabled(), true);
  await createModal().getByLabel("Workspace name").fill("September 2026 · TCB review");
  await screenshot("create-dark");
  failCreate = true;
  await createModal().getByRole("button", { name: "Create workspace", exact: true }).click();
  await createModal().getByRole("alert").waitFor();
  assert.equal(await createModal().getByLabel("Workspace name").inputValue(), "September 2026 · TCB review");
  failCreate = false;
  await createModal().getByRole("button", { name: "Create workspace", exact: true }).click();
  await page.waitForURL("**/workspaces/1");
  await page.getByRole("heading", { name: "Your workspace is ready" }).waitFor();
  assert.equal(uploads.length, 0);
  await screenshot("upload-dark");
  await page.reload();
  await page.getByRole("heading", { name: "September 2026 · TCB review" }).waitFor();
  await page.getByRole("link", { name: "All workspaces", exact: false }).click();
  await page.locator(".ws-workspace-card").first().waitFor();
  await create("October 2026 · TCB review");
  assert.equal(batches.length, 2);
  await page.getByRole("link", { name: "All workspaces", exact: false }).click();
  await page.locator(".ws-workspace-card").first().waitFor();
  await page.getByLabel("Search workspaces").fill("September");
  assert.equal(await page.locator(".ws-workspace-card").count(), 1);
  await page.locator(".ws-workspace-card").click();
  await page.getByRole("heading", { name: "Your workspace is ready" }).waitFor();

  await page.getByLabel("BOM datasheet", { exact: true }).setInputFiles({ name: "wrong.pdf", mimeType: "application/pdf", buffer: Buffer.from("invalid") });
  await page.getByText("Choose a CSV, XLSX, or XLS datasheet.", { exact: true }).waitFor();
  const csv = { name: "review.csv", mimeType: "text/csv", buffer: Buffer.from("item_id,max_qty,rop_qty,min_qty,unitprice\nP1,4,2,1,10") };
  // Exercise the actual drag/drop handlers as well as the file picker above.
  const transfer = await page.evaluateHandle(({ name, text }) => { const data = new DataTransfer(); data.items.add(new File([text], name, { type: "text/csv" })); return data; }, { name: csv.name, text: csv.buffer.toString() });
  await page.locator(".ws-dropzone").dispatchEvent("drop", { dataTransfer: transfer });
  await page.getByRole("heading", { name: "review.csv", exact: true }).waitFor();
  failUpload = true;
  await page.getByRole("button", { name: "Upload & start review", exact: true }).click();
  await page.getByText("Missing required columns: item_id", { exact: true }).waitFor();
  assert.equal(await page.getByRole("heading", { name: "review.csv", exact: true }).count(), 1);
  failUpload = false; failScore = true;
  await page.getByRole("button", { name: "Upload & start review", exact: true }).click();
  await page.getByText("Engine unavailable. Try again.", { exact: true }).waitFor();
  assert.equal(uploads.length, 1);
  assert.equal(batches.find((batch) => batch.batch_id === 2).status, "draft");
  assert.equal(await page.getByLabel("BOM datasheet").count(), 0, "Scoring error must not invite duplicate upload");
  await page.reload();
  await page.getByRole("heading", { name: "Your datasheet is in place" }).waitFor();
  await screenshot("loaded-dark");
  await page.setViewportSize({ width: 390, height: 844 }); await noOverflow(); await screenshot("loaded-mobile");
  await page.setViewportSize({ width: 1440, height: 1000 });
  failScore = false;
  // Stop at the existing queue page; this test owns only workspace setup.
  await page.route("**/batches/1", (route) => route.request().resourceType() === "document" ? route.fulfill({ contentType: "text/html", body: "<h1>Review queue</h1>" }) : route.continue());
  await page.getByRole("button", { name: "Run recommendations", exact: true }).click();
  await page.waitForURL("**/batches/1");
  assert.deepEqual(scores, [1, 1]);
  assert.equal(uploads.length, 1);
  await page.goto(base);
  await page.locator(".ws-workspace-card").first().waitFor();
  await screenshot("list-dark");
  await page.emulateMedia({ colorScheme: "light" }); await screenshot("list-light");
  await page.getByRole("button", { name: "Awaiting datasheet", exact: true }).click();
  assert.equal(await page.locator(".ws-workspace-card").count(), 1);
  await page.getByLabel("Search workspaces").fill("missing");
  await page.getByRole("heading", { name: "No workspaces found" }).waitFor();
  await page.getByRole("button", { name: "Clear filters", exact: true }).click();
  assert.equal(await page.locator(".ws-workspace-card").count(), 2);
  await page.locator(".ws-workspace-card").filter({ hasText: "October" }).click();
  await page.getByLabel("BOM datasheet", { exact: true }).setInputFiles(csv);
  await page.getByLabel("Run recommendations after upload", { exact: true }).uncheck();
  await page.getByRole("button", { name: "Upload datasheet", exact: true }).click();
  await page.getByRole("heading", { name: "Your datasheet is in place" }).waitFor();
  assert.deepEqual(scores, [1, 1], "Ingest-only skips scoring");
  assert.equal(uploads.length, 2);
  failLoad = true;
  await page.goto(base);
  await page.getByRole("alert").waitFor();
  assert.equal(await page.getByRole("heading", { name: "Your next BOM review starts here" }).count(), 0);
  failLoad = false;
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await page.locator(".ws-workspace-card").first().waitFor();
  batches.push({ ...batches[0], batch_id: 50, label: "Shared TCB draft", uploaded_by: "tcb-1", status: "draft" });
  await page.reload();
  const sharedCard = page.locator(".ws-workspace-card").filter({ hasText: "Shared TCB draft" });
  await sharedCard.getByText("Owner: tcb-1 · Read-only").waitFor();
  await sharedCard.click();
  await page.getByText("Viewing tcb-1's workspace. You have read-only access.").waitFor();
  assert.equal(await page.getByRole("button", { name: "Browse files", exact: true }).isDisabled(), true);
  assert.equal(await page.getByLabel("BOM datasheet", { exact: true }).isDisabled(), true);
  assert.equal(uploads.length, 2, "Shared viewing must not upload a datasheet");
  assert.deepEqual(errors, []);
  console.log("PASS: empty states, themes, mobile, guide/focus, creation validation and recovery, persisted drafts, independent cycles, search/filters, file checks, drag/drop, upload errors, scoring retry without duplicate upload, ingest-only, and load recovery.");
} finally { await browser.close(); }
