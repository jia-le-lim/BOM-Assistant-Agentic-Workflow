/** Browser coverage for screenshot capture, correction, persistence UI, and due reminders.
 * API traffic is intercepted; no engineer data is written by this check.
 */
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

const base = process.env.BASE_URL ?? "http://127.0.0.1:3011";
const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAEAAAAAoCAIAAADBrGu+AAAAXElEQVR4nNXOQREAIAzAsFJXKEQpPhCxB9coyNrnUiZxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEidxEufvwNQDa2QB9djnbJsAAAAASUVORK5CYII=", "base64");
let browser;
try { browser = await chromium.launch(); }
catch (e) { if (process.platform !== "win32") throw e; browser = await chromium.launch({ channel: "msedge" }); }
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });
const page = await context.newPage();
await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: base });
async function pasteImage(input) {
  await page.evaluate(async (encoded) => {
    const bytes = Uint8Array.from(atob(encoded), (c) => c.charCodeAt(0));
    await navigator.clipboard.write([new ClipboardItem({ "image/png": new Blob([bytes], { type: "image/png" }) })]);
  }, png.toString("base64"));
  await input.focus();
  await input.press("Control+V");
}

const errors = [], records = [], writes = [];
let extractFailed = false;
page.on("pageerror", (e) => errors.push(e.message));
await page.route("**/api/pilot-session", (route) => route.fulfill({ json: { required: true, user: "Alice", role: "admin" } }));
await page.route("**/api/backend/**", async (route) => {
  const req = route.request(), url = new URL(req.url()), path = url.pathname.split("/api/backend/")[1];
  if (path === "reminders/extract") {
    writes.push(path);
    return route.fulfill({ json: {
      extraction: { title: "Review ROP increase", item_id: "500095408", stockroom_id: null,
        description: "Solenoid valve", request_text: "later free tolong increase ROP this part",
        current_max: 1, current_rop: 0, proposed_change_text: "2:1 pon okey kut",
        uncertainties: ["Confirm the part number and what 2:1 means."] },
      model: "vision-fixture", warning: extractFailed ? "Image reading unavailable. Enter details manually." : null,
    } });
  }
  if (path === "reminders" && req.method() === "POST") {
    writes.push(path);
    const text = req.postDataBuffer().toString("utf8");
    const match = text.match(/name="payload"\r\n\r\n([\s\S]*?)\r\n--/);
    assert.ok(match, "Save sends structured draft data");
    const data = JSON.parse(match[1]);
    const row = { ...data, reminder_id: data.request_id, owner_user: "Alice", status: "open",
      created_at: "2026-09-28 01:00:00", updated_at: "2026-09-28 01:00:00",
      has_image: text.includes('name="file"'), matched_batch_id: null, is_due: data.timing === "date" };
    records.push(row);
    return route.fulfill({ status: 201, json: row });
  }
  if (path === "reminders") {
    let rows = records.filter((r) => url.searchParams.get("status") === "all" || r.status === (url.searchParams.get("status") ?? "open"));
    if (url.searchParams.get("due_only") === "true") rows = rows.filter((r) => r.is_due && r.status === "open");
    const total = rows.length, offset = Number(url.searchParams.get("offset") ?? 0), limit = Number(url.searchParams.get("limit") ?? 50);
    return route.fulfill({ json: { reminders: rows.slice(offset, offset + limit), total } });
  }
  const action = /^reminders\/([^/]+)\/(status|edit|image)$/.exec(path);
  if (action) {
    const row = records.find((r) => r.reminder_id === action[1]);
    assert.ok(row);
    if (action[2] === "image") return route.fulfill({ contentType: "image/png", body: png });
    writes.push(path);
    Object.assign(row, req.postDataJSON());
    row.is_due = row.status === "open" && row.timing === "date";
    return route.fulfill({ json: row });
  }
  if (path === "health") return route.fulfill({ json: { llm_provider: "fixture", llm_model: "NYRA" } });
  if (path === "chat/sessions") return route.fulfill({ json: { sessions: [] } });
  if (path === "chat/skills") return route.fulfill({ json: { skills: [] } });
  if (path === "pending-changes") return route.fulfill({ json: { pending: [], count: 0 } });
  if (path === "batches") return route.fulfill({ json: [] });
  return route.fulfill({ status: 404, json: { detail: "No fixture for " + path } });
});
mkdirSync(".local/reminders", { recursive: true });
try {
  await page.goto(base + "/chat");
  await page.getByRole("button", { name: "Capture image reminder" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Reminder screenshot").setInputFiles({ name: "request.png", mimeType: "image/png", buffer: png });
  await page.waitForFunction(() => document.querySelector('input[placeholder="Review request to increase ROP"]')?.value === "Review ROP increase");
  assert.equal(records.length, 0, "Extraction does not save");
  assert.equal(await dialog.getByRole("button", { name: "Save reminder", exact: true }).isDisabled(), true);
  assert.equal(await dialog.getByLabel("Stockroom", { exact: true }).inputValue(), "", "Ambiguous stockroom stays blank");
  await dialog.getByLabel("Part number", { exact: true }).fill("500099408");
  await dialog.getByLabel("Stockroom", { exact: true }).fill("24");
  await dialog.getByLabel("I checked the screenshot", { exact: false }).check();
  await page.screenshot({ path: ".local/reminders/capture-desktop.png", fullPage: true });
  await dialog.getByRole("button", { name: "Save reminder", exact: true }).click();
  await dialog.waitFor({ state: "hidden" });
  assert.equal(records[0].item_id, "500099408");
  assert.equal(records[0].stockroom_id, "24");
  assert.equal(records[0].due_date, null, "No due date invented from the screenshot");
  assert.equal(records[0].has_image, true);
  await page.getByRole("link", { name: "Engineer reminders", exact: true }).click();
  await page.getByRole("heading", { name: "Review ROP increase", exact: true }).waitFor();
  await page.reload();
  await page.getByRole("heading", { name: "Review ROP increase", exact: true }).waitFor();
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await dialog.getByLabel("Remind me", { exact: true }).selectOption("date");
  await dialog.getByLabel("Reminder date", { exact: true }).fill("2026-09-28");
  await dialog.getByRole("button", { name: "Save changes", exact: true }).click();
  await dialog.waitFor({ state: "hidden" });
  await page.getByRole("button", { name: "Due now", exact: true }).click();
  await page.getByRole("heading", { name: "Review ROP increase", exact: true }).waitFor();
  await page.getByRole("button", { name: "View screenshot", exact: true }).click();
  await page.getByAltText("Original reminder screenshot").waitFor();
  await page.screenshot({ path: ".local/reminders/list-desktop.png", fullPage: true });
  await page.getByRole("button", { name: "Mark complete", exact: true }).click();
  await page.getByRole("button", { name: "Completed", exact: true }).click();
  await page.getByRole("heading", { name: "Review ROP increase", exact: true }).waitFor();
  await page.getByRole("button", { name: "Reopen", exact: true }).click();
  await page.getByRole("button", { name: "Open", exact: true }).click();
  await page.getByRole("button", { name: "Dismiss", exact: true }).click();
  await page.getByRole("button", { name: "Dismissed", exact: true }).click();
  await page.getByRole("heading", { name: "Review ROP increase", exact: true }).waitFor();

  // Actual Ctrl+V attaches locally in the typing area and waits for Send.
  await page.goto(base + "/chat");
  const input = page.getByLabel("Ask about a recommendation", { exact: true });
  const attachment = page.getByRole("group", { name: "Attached image", exact: true });
  await input.waitFor();
  await input.fill("Please check this request next cycle.");
  const beforePaste = writes.length;
  await pasteImage(input);
  await attachment.waitFor();
  assert.equal(await dialog.count(), 0, "Paste stays in the typing space");
  assert.equal(writes.length, beforePaste, "Pasting does not send the image");
  assert.equal(await input.inputValue(), "Please check this request next cycle.");
  await page.screenshot({ path: ".local/reminders/pasted-chat-image.png", fullPage: true });
  await page.getByRole("button", { name: "Remove attached image" }).click();
  await attachment.waitFor({ state: "hidden" });
  assert.equal(await input.inputValue(), "Please check this request next cycle.");
  await pasteImage(input);
  await attachment.waitFor();
  // Invalid replacements preserve the valid attachment and report the problem.
  await input.evaluate((el) => {
    const transfer = new DataTransfer();
    transfer.items.add(new File([new Uint8Array(5 * 1024 * 1024 + 1)], "large.png", { type: "image/png" }));
    el.dispatchEvent(new ClipboardEvent("paste", { clipboardData: transfer, bubbles: true, cancelable: true }));
  });
  await page.getByRole("alert").filter({ hasText: "Choose an image up to 5 MB." }).waitFor();
  await attachment.waitFor();
  assert.equal(writes.length, beforePaste);
  await pasteImage(input);
  await input.press("Enter");
  await dialog.waitFor();
  await page.waitForFunction(() => document.querySelector('input[placeholder="Review request to increase ROP"]')?.value === "Review ROP increase");
  assert.match(await dialog.getByLabel("Request and notes", { exact: true }).inputValue(), /Engineer note: Please check this request next cycle/);
  await dialog.getByRole("button", { name: "Close reminder", exact: true }).click();
  await attachment.waitFor();
  assert.equal(records.length, 1, "Cancelling keeps the attachment but does not save");
  // Image-only Send is enabled; cancelling also retains it.
  await input.fill("");
  assert.equal(await page.getByRole("button", { name: "Send", exact: true }).isEnabled(), true);
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await dialog.waitFor();
  await page.keyboard.press("Escape");
  await dialog.waitFor({ state: "hidden" });
  await attachment.waitFor();
  // Text-only clipboard payloads retain normal browser paste behavior.
  await page.evaluate(() => navigator.clipboard.writeText("Ordinary pasted text"));
  await input.focus();
  await input.press("Control+V");
  assert.equal(await input.inputValue(), "Ordinary pasted text");
  await page.getByRole("button", { name: "Remove attached image" }).click();

  // Clipboard items fallback: some browsers expose items without FileList.
  await input.evaluate((el, b64) => {
    const image = new File([Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))], "items-only.png", { type: "image/png" });
    const event = new Event("paste", { bubbles: true, cancelable: true });
    Object.defineProperty(event, "clipboardData", { value: {
      files: [], items: [{ kind: "file", type: "image/png", getAsFile: () => image }],
    } });
    el.dispatchEvent(event);
  }, png.toString("base64"));
  await attachment.waitFor();
  await page.getByRole("button", { name: "New conversation", exact: true }).click();
  await attachment.waitFor({ state: "hidden" });

  // The floating chatbot has the same typing-space attachment behavior.
  await page.goto(base + "/reminders");
  await page.locator(".assistant-float > summary").click();
  const floating = page.getByRole("region", { name: "NYRA review assistant" });
  const floatingInput = floating.getByLabel("Ask NYRA", { exact: true });
  await floatingInput.fill("Please follow up with the requesting engineer.");
  const beforeFloating = writes.length;
  await pasteImage(floatingInput);
  await attachment.waitFor();
  assert.equal(writes.length, beforeFloating);
  assert.equal(await dialog.count(), 0);
  await page.screenshot({ path: ".local/reminders/pasted-floating-image.png", fullPage: true });
  await floating.getByRole("button", { name: "Send", exact: true }).click();
  await dialog.waitFor();
  await page.waitForFunction(() => document.querySelector('input[placeholder="Review request to increase ROP"]')?.value === "Review ROP increase");
  assert.match(await dialog.getByLabel("Request and notes", { exact: true }).inputValue(), /Engineer note: Please follow up/);
  await page.keyboard.press("Escape");
  await dialog.waitFor({ state: "hidden" });
  assert.equal(await page.locator(".assistant-float").evaluate((el) => el.open), true, "Escape only closes the reminder preview");
  await attachment.waitFor();
  await floating.getByRole("button", { name: "Send", exact: true }).click();
  await dialog.getByLabel("I checked the screenshot", { exact: false }).check();
  await dialog.getByRole("button", { name: "Save reminder", exact: true }).click();
  await dialog.waitFor({ state: "hidden" });
  await attachment.waitFor({ state: "hidden" });
  assert.equal(await floatingInput.inputValue(), "", "A successful save clears the draft");
  assert.equal(records.length, 2);
  // Manual fallback and responsive layout.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(base + "/reminders");
  await page.getByRole("button", { name: "New reminder", exact: true }).click();
  extractFailed = true;
  await dialog.getByLabel("Reminder screenshot").setInputFiles({ name: "request.png", mimeType: "image/png", buffer: png });
  await dialog.getByText("Image reading unavailable. Enter details manually.", { exact: true }).waitFor();
  await page.screenshot({ path: ".local/reminders/capture-mobile.png", fullPage: true });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, "No horizontal overflow on mobile");
  await dialog.getByLabel("Reminder title", { exact: true }).fill("Follow up with engineer");
  await dialog.getByLabel("I checked the screenshot", { exact: false }).check();
  await dialog.getByRole("button", { name: "Save reminder", exact: true }).click();
  await dialog.waitFor({ state: "hidden" });
  await page.getByRole("heading", { name: "Follow up with engineer" }).waitFor();
  assert.equal(records.length, 3);
  assert.deepEqual(errors, []);
  console.log("Reminder browser checks passed: extraction preview, correction, save, reload, dates, evidence, lifecycle, actual Ctrl+V in both chat boxes, image-only Send, draft retention, normal text paste, size validation, manual fallback, and mobile layout.");
} finally {
  await browser.close();
}
