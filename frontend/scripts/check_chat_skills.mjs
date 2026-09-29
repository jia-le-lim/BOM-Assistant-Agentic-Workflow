/** Slash picker and activity integration, using isolated backend fixtures. */
import assert from "node:assert/strict";
import { mkdirSync, readFileSync } from "node:fs";
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
let failSkills = false, standard = false, holdStream = null;
const titles = [
  ["brief", "Workspace brief", "/brief"], ["triage", "Triage the queue", "/triage [count]"],
  ["explain", "Explain a recommendation", "/explain <item> [stockroom <id>]"],
  ["history", "Review history", "/history <item>"],
  ["review-note", "Draft a review note", "/review-note <item> [stockroom <id>]"],
  ["propose", "Propose a change", "/propose <item> max <value>"],
  ["dormant-check", "Check dormant coverage", "/dormant-check"],
  ["peers", "Compare peer evidence", "/peers <item>"],
];
const skills = titles.map(([name, title, usage]) => ({ name, title, usage, available: name !== "propose",
  unavailable_reason: name === "propose" ? "Requires a review role" : null,
  description: readFileSync(new URL(`../../backend/app/agent/skills/${name}/SKILL.md`, import.meta.url), "utf8").match(/^description: (.+)$/m)[1],
}));
const brief = { name: "brief", title: "Workspace brief" };
const calls = [{ name: "batch_summary", args: { batch_id: 41 }, ok: true }, { name: "top_exposure", args: { batch_id: 41, n: 5 }, ok: true }];
await page.route("**/api/pilot-session", (route) => route.fulfill({ json: { required: true, user: "Iris", role: "admin" } }));
await page.route("**/api/backend/**", async (route) => {
  const path = new URL(route.request().url()).pathname.split("/api/backend/")[1];
  if (path === "health") return route.fulfill({ json: { llm_provider: "fixture", llm_model: "NYRA" } });
  if (path === "pending-changes") return route.fulfill({ json: { pending: [], count: 0 } });
  if (path === "batches") return route.fulfill({ json: [{ batch_id: 41, label: "January BOM review", status: "scored", module_filter: "TCB" }] });
  if (path === "chat/skills") return route.fulfill(failSkills ? { status: 503, json: { detail: "Temporary error" } } : { json: { skills } });
  if (path === "chat/sessions") return route.fulfill({ json: { sessions: [] } });
  if (path === "chat/sessions/saved") return route.fulfill({ json: { session_id: "saved", turns: [{
    turn_id: 1, batch_id: 41, question: "/brief", answer: "Saved workspace brief.", skill: brief,
    provider: "fixture", model: "NYRA", tool_calls: calls,
  }] } });
  if (path === "chat/stream" && standard) return route.fulfill({ status: 404, json: { detail: "Streaming unavailable" } });
  if (path === "chat/stream" || path === "chat") {
    const body = route.request().postDataJSON();
    requests.push(body);
    if (holdStream) await holdStream;
    if (body.question === "/explain") return route.fulfill({ contentType: "application/x-ndjson", body: JSON.stringify({ type: "error", stage: "request", status: 422, error_type: "HTTPException", message: "Usage: /explain <item> [stockroom <id>]" }) + "\n" });
    const isSkill = body.question.startsWith("/");
    const response = { type: "complete", answer: isSkill ? "## Workspace brief\n\nJanuary BOM review has **12 items awaiting review**. Pending exposure is **$8,400**.\n\nStart with the highest-exposure items in this workspace." : "Ordinary follow-up answer.", batch_id: body.batch_id ?? 41,
      sources: [{ type: "batches", batch_id: 41 }], session_id: body.session_id ?? "skill-session", turn_id: requests.length,
      provider: "fixture", model: "NYRA", tool_calls: isSkill ? calls : [], skill: isSkill ? brief : null };
    const events = [
      { type: "request", query: body.question, batch_id: response.batch_id, provider: "fixture", model: "NYRA" },
      ...(isSkill ? [{ type: "skill", ...brief }, ...calls.flatMap((call, index) => [
        { type: "tool_start", sequence: index + 1, name: call.name, args: call.args },
        { type: "tool_result", sequence: index + 1, name: call.name, status: "ok", summary: "Retrieved workspace records" },
      ])] : []), response,
    ];
    return path === "chat" ? route.fulfill({ json: response }) : route.fulfill({ contentType: "application/x-ndjson", body: events.map((event) => JSON.stringify(event)).join("\n") + "\n" });
  }
  return route.fulfill({ status: 404, json: { detail: "No fixture" } });
});
const input = page.getByLabel("Ask about a recommendation", { exact: true });
const menu = page.getByRole("listbox", { name: "Skills", exact: true });
const activity = page.locator("#chat-activity");
async function open(suffix = "") {
  await page.goto(base + "/chat" + suffix);
  await input.waitFor();
  await page.waitForLoadState("networkidle");
}
async function send(question) {
  const before = requests.length;
  await input.fill(question);
  await input.press("Enter");
  await page.waitForFunction(() => !document.querySelector(".chat-thinking"));
  assert.equal(requests.length, before + 1);
}
mkdirSync(".local/chat-skills", { recursive: true });
try {
  await open();
  await input.fill("/");
  assert.equal(await menu.getByRole("option").count(), 8);
  await input.press("ArrowDown");
  assert.equal(await input.getAttribute("aria-activedescendant"), "skill-option-triage");
  await input.press("Tab");
  assert.equal(await input.inputValue(), "/triage ");
  assert.equal(requests.length, 0, "Choosing a skill never submits a request");
  assert.match(await page.locator("#composer-skill-help").textContent(), /\/triage \[count\]/);
  await input.fill("/explain");
  await menu.getByRole("option", { name: /Explain a recommendation/ }).waitFor();
  assert.equal(await input.getAttribute("aria-activedescendant"), "skill-option-explain", "Command names rank ahead of descriptions");
  await input.press("Enter");
  assert.match(await page.locator("#composer-skill-help").textContent(), /<item>/);
  await input.fill("/propose");
  assert.equal(await menu.getByRole("option").first().getAttribute("aria-disabled"), "true");
  await input.press("Enter");
  assert.equal(await input.inputValue(), "/propose");
  assert.equal(requests.length, 0);
  await input.press("Escape");
  assert.equal(await menu.count(), 0);
  await input.fill("/no-such-skill");
  await page.getByText("No matching skills. Try / to see all skills.").waitFor();
  await input.press("Enter");
  assert.equal(requests.length, 0);
  await input.fill("Open https://example.com/brief");
  assert.equal(await menu.count(), 0);
  console.log("PASS slash search, keyboard selection, usage hints, disabled skills, Escape, and ordinary URLs");

  await input.fill("/brief @January");
  await page.getByRole("listbox", { name: "Workspaces", exact: true }).getByRole("option").click();
  assert.equal(await input.inputValue(), "/brief ");
  assert.match(await page.getByLabel("Workspace focus", { exact: true }).textContent(), /January BOM review/);
  await input.press("Enter");
  await activity.getByText("Loaded skill instructions").waitFor();
  assert.equal(requests.at(-1).question, "/brief");
  assert.equal(requests.at(-1).batch_id, 41);
  assert.equal(requests.at(-1).page_context.batch_id, 41);
  assert.equal(await activity.locator(".activity-tool-name").count(), 2);
  await activity.getByText("View input", { exact: true }).first().click();
  assert.match(await activity.locator("pre").first().textContent(), /"batch_id": 41/);
  await page.screenshot({ path: ".local/chat-skills/skill-response.png", fullPage: true });
  await send("Explain the highest exposure");
  assert.equal(requests.at(-1).session_id, "skill-session");
  assert.equal(await activity.getByText("Loaded skill instructions").count(), 0, "A skill does not replace later ordinary chat");
  standard = true;
  await send("/brief ");
  assert.equal(await activity.locator(".activity-tool-name").count(), 2);
  await activity.getByText("Loaded skill instructions").waitFor();
  standard = false;
  console.log("PASS / with @, raw command delivery, tool inputs, ordinary follow-ups, and standard response fallback");

  await send("/explain ");
  assert.equal(await input.inputValue(), "/explain", "Invalid commands remain editable");
  assert.match(await activity.textContent(), /Failed/);
  let release;
  holdStream = new Promise((resolve) => { release = resolve; });
  await input.fill("/brief ");
  await input.press("Enter");
  await page.locator(".chat-thinking").waitFor();
  assert.equal(await page.getByRole("button", { name: "Choose skill", exact: true }).isDisabled(), true);
  release(); holdStream = null;
  await page.waitForFunction(() => !document.querySelector(".chat-thinking"));
  await open("?session=saved");
  await page.getByText("Saved workspace brief.", { exact: true }).waitFor();
  await activity.getByText("Saved skill run", { exact: true }).waitFor();
  assert.equal(await activity.locator(".activity-tool-name").count(), 2);
  console.log("PASS input recovery, in-flight controls, and restored skill activity");

  await input.fill("100005");
  await page.getByRole("button", { name: "Choose skill", exact: true }).click();
  await menu.getByRole("option", { name: /Explain a recommendation/ }).click();
  assert.equal(await input.inputValue(), "/explain 100005", "The slash button preserves existing arguments");
  await input.fill("/");
  for (let index = 0; index < 7; index++) await input.press("ArrowDown");
  for (const theme of ["dark", "light"]) {
    await page.emulateMedia({ colorScheme: theme });
    await page.waitForFunction((color) => getComputedStyle(document.querySelector("#chat-activity-title")).color === color,
      theme === "light" ? "rgb(11, 11, 11)" : "rgb(255, 255, 255)");
    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({ width, height: 850 });
      await page.waitForFunction(() => {
        const box = document.querySelector(".skill-picker-menu").getBoundingClientRect();
        const active = document.getElementById(document.querySelector("#ask").getAttribute("aria-activedescendant")).getBoundingClientRect();
        const list = document.querySelector("#skill-options").getBoundingClientRect();
        return box.x >= 0 && box.y >= 0 && box.right <= innerWidth + 1 && box.bottom <= innerHeight
          && active.top >= list.top - 1 && active.bottom <= list.bottom + 1;
      });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
      assert.equal(await activity.locator("h2").evaluate((heading) => getComputedStyle(heading).color),
        theme === "light" ? "rgb(11, 11, 11)" : "rgb(255, 255, 255)", "Activity headings follow the active theme");
      const activeBox = await page.locator("#" + await input.getAttribute("aria-activedescendant")).boundingBox();
      const listBox = await menu.boundingBox();
      assert.ok(activeBox.y >= listBox.y - 1 && activeBox.y + activeBox.height <= listBox.y + listBox.height + 1,
        JSON.stringify({ theme, width, activeBox, listBox }));
      await page.screenshot({ path: `.local/chat-skills/${theme}-${width}.png`, fullPage: true });
    }
  }
  console.log("PASS slash button argument preservation and scrolling at desktop/mobile widths in both themes");

  failSkills = true;
  await open();
  await input.fill("/");
  await page.getByText("Could not load skills.", { exact: false }).waitFor();
  failSkills = false;
  await page.getByRole("button", { name: "Try again", exact: true }).click();
  await menu.getByRole("option").first().waitFor();
  assert.equal(await menu.getByRole("option").count(), 8);
  assert.deepEqual(errors, []);
  console.log("PASS catalog error recovery and no browser exceptions");
} finally { await browser.close(); }
