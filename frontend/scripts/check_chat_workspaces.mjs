/** Workspace mentions: keyboard selection and real request scoping, using isolated fixtures. */
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
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark", reducedMotion: "reduce" });
const page = await context.newPage();
const errors = [], requests = [];
page.on("pageerror", (error) => errors.push(error.message));
let failWorkspaces = false, emptyWorkspaces = false, holdStream = null, standardResponse = false;
const workspaces = [
  { batch_id: 41, label: "January 2026 · TCB review", module_filter: "TCB", status: "scored" },
  { batch_id: 52, label: "February 2026 · Epoxy review", module_filter: "Epoxy", status: "scored" },
  { batch_id: 63, label: "September planning", module_filter: "TCB", status: "draft" },
  ...Array.from({ length: 12 }, (_, index) => ({ batch_id: 100 + index, label: `Historical workspace ${index + 1}`, module_filter: "ALL", status: "scored" })),
];
await page.route("**/api/pilot-session", (route) => route.fulfill({ json: { required: true, user: "Iris", role: "admin" } }));
await page.route("**/api/backend/**", async (route) => {
  const path = new URL(route.request().url()).pathname.split("/api/backend/")[1];
  if (path === "health") return route.fulfill({ json: { llm_provider: "fixture", llm_model: "NYRA" } });
  if (path === "pending-changes") return route.fulfill({ json: { pending: [], count: 0 } });
  if (path === "batches") return route.fulfill(failWorkspaces ? { status: 503, json: { detail: "Temporary error" } } : { json: emptyWorkspaces ? [] : workspaces });
  if (path === "chat/sessions") return route.fulfill({ json: { sessions: [] } });
  if (path === "chat/skills") return route.fulfill({ json: { skills: [] } });
  if (path === "chat/sessions/saved") return route.fulfill({ json: { session_id: "saved", turns: [41, 52].map((batch_id, index) => ({
    turn_id: index + 1, batch_id, question: `Saved question ${index + 1}`, answer: `Saved answer ${index + 1}`,
    provider: "fixture", model: "NYRA", tool_calls: [],
  })) } });
  if (path === "chat/stream" && standardResponse) return route.fulfill({ status: 404, json: { detail: "Streaming unavailable" } });
  if (path === "chat/stream" || path === "chat") {
    const body = route.request().postDataJSON();
    requests.push(body);
    if (holdStream) await holdStream;
    const batch_id = body.batch_id ?? 52;
    const response = { type: "complete", answer: `Answer from workspace ${batch_id}.`, batch_id, sources: [{ type: "batches", batch_id }],
      session_id: body.session_id ?? "focused-session", turn_id: requests.length, provider: "fixture", model: "NYRA", tool_calls: [] };
    return path === "chat" ? route.fulfill({ json: response }) : route.fulfill({ contentType: "application/x-ndjson", body: JSON.stringify(response) + "\n" });
  }
  return route.fulfill({ status: 404, json: { detail: "No fixture" } });
});
const input = page.getByLabel("Ask about a recommendation", { exact: true });
const focus = page.getByLabel("Workspace focus", { exact: true });
const menu = page.getByRole("listbox", { name: "Workspaces", exact: true });
async function open() {
  await page.goto(base + "/chat");
  await page.getByRole("heading", { name: "What are we reviewing?" }).waitFor();
  await page.waitForLoadState("networkidle");
}
async function select(search, name) {
  await input.fill("@" + search);
  await menu.getByRole("option", { name }).waitFor();
  await input.press("Enter");
  await focus.waitFor();
  assert.equal(await input.inputValue(), "");
}
async function send(question, workspace) {
  const count = requests.length;
  await input.fill(question);
  await input.press("Enter");
  await page.waitForFunction(() => !document.querySelector(".chat-thinking"));
  assert.equal(requests.length, count + 1);
  assert.equal(requests.at(-1).question, question);
  assert.equal(requests.at(-1).batch_id, workspace);
  assert.equal(requests.at(-1).page_context.batch_id, workspace);
  assert.equal(requests.at(-1).page_context.path, "/chat");
}
mkdirSync(".local/chat-workspaces", { recursive: true });
try {
  await open();
  await select("January 2026", /January 2026/);
  assert.equal(requests.length, 0, "Selecting a workspace must not send a question");
  assert.match(await focus.textContent(), /January 2026/);
  await send("Summarise this workspace", 41);
  assert.match(requests.at(-1).page_context.title, /January 2026/);
  await send("Which items should I review first?", 41);
  assert.equal(requests.at(-1).session_id, "focused-session");
  assert.equal(await page.locator(".chat-message-workspace").count(), 2);
  console.log("PASS @ name search, selection without sending, pinned follow-ups, and per-message workspace labels");

  await page.getByRole("button", { name: "Choose workspace", exact: true }).click();
  await menu.waitFor();
  await input.press("ArrowDown");
  assert.equal(await input.getAttribute("aria-activedescendant"), "workspace-option-52");
  await input.press("Tab");
  assert.match(await focus.textContent(), /February 2026/);
  await send("Check this workspace instead", 52);
  await page.getByRole("button", { name: "Clear workspace focus" }).click();
  assert.equal(await focus.count(), 0);
  await send("Use the latest workspace", null);
  assert.match(await focus.textContent(), /February 2026/, "Pin the backend's resolved default workspace");
  console.log("PASS @ button, arrow keys, Tab, switching workspace, and explicit default context");

  await select("September", /September planning/);
  assert.match(await page.locator("#workspace-focus-help").textContent(), /awaiting a datasheet/);
  standardResponse = true;
  await send("Summarise the draft", 63);
  standardResponse = false;
  await input.fill("@does-not-exist");
  await page.getByText("No matching workspaces. Try another name or module.").waitFor();
  const before = requests.length;
  await input.press("Enter");
  assert.equal(requests.length, before);
  await input.press("Escape");
  assert.equal(await menu.count(), 0);
  await input.fill("Contact engineer@example.com");
  assert.equal(await menu.count(), 0, "Emails are not workspace mentions");
  await input.fill("Review @January please");
  await input.press("Home");
  for (let index = 0; index < 15; index++) await input.press("ArrowRight");
  await menu.getByRole("option", { name: /January 2026/ }).click();
  assert.equal(await input.inputValue(), "Review  please", "Selecting a mention preserves text before and after the caret");
  console.log("PASS draft focus on standard response path, no-match handling, Escape, emails, and text preservation");

  let release;
  holdStream = new Promise((resolve) => { release = resolve; });
  await input.fill("Keep this question in January");
  await input.press("Enter");
  await page.locator(".chat-thinking").waitFor();
  assert.equal(await page.getByRole("button", { name: "Clear workspace focus" }).isDisabled(), true);
  assert.equal(await page.getByRole("button", { name: "Choose workspace", exact: true }).isDisabled(), true);
  release(); holdStream = null;
  await page.waitForFunction(() => !document.querySelector(".chat-thinking"));
  assert.equal(requests.at(-1).batch_id, 41);
  await page.goto(base + "/chat?session=saved");
  await page.getByText("Saved answer 2", { exact: true }).waitFor();
  assert.match(await focus.textContent(), /February 2026/);
  await send("Continue this saved review", 52);
  assert.equal(requests.at(-1).session_id, "saved");
  await page.getByRole("button", { name: "New chat", exact: true }).click();
  assert.equal(await focus.count(), 0);
  console.log("PASS in-flight scope stays fixed, saved conversations restore focus, and new chat clears it");

  await input.fill("@");
  await menu.waitFor();
  for (let index = 0; index < 12; index++) await input.press("ArrowDown");
  const active = page.locator("#" + await input.getAttribute("aria-activedescendant"));
  const activeBox = await active.boundingBox(), listBox = await menu.boundingBox();
  assert.ok(activeBox.y >= listBox.y && activeBox.y + activeBox.height <= listBox.y + listBox.height + 1);
  for (const theme of ["dark", "light"]) {
    await page.emulateMedia({ colorScheme: theme });
    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({ width, height: 850 });
      await page.waitForFunction(() => {
        const box = document.querySelector(".workspace-mention-menu").getBoundingClientRect();
        return box.x >= 0 && box.y >= 0 && box.right <= innerWidth + 1;
      });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
      const box = await page.locator(".workspace-mention-menu").boundingBox();
      await page.screenshot({ path: `.local/chat-workspaces/${theme}-${width}.png`, fullPage: true });
      assert.ok(box.x >= 0 && box.y >= 0 && box.x + box.width <= width + 1, JSON.stringify({ width, box }));
    }
  }
  console.log("PASS keyboard scrolling and picker layout on desktop/mobile in both themes");

  failWorkspaces = true;
  await open();
  await input.fill("@");
  await page.getByText("Could not load workspaces.", { exact: false }).waitFor();
  failWorkspaces = false;
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await menu.getByRole("option", { name: /January 2026/ }).waitFor();
  emptyWorkspaces = true;
  await open();
  await input.fill("@");
  await page.getByText("No workspaces yet. Create one from Workspaces to get started.").waitFor();
  emptyWorkspaces = false;
  await open();
  await select("41", /January 2026/);
  await send("Explain the current recommendation", 41);
  assert.deepEqual(errors, []);
  console.log("PASS list failure recovery, empty list, workspace ID search, and no browser exceptions");
} finally { await browser.close(); }
