/** Saved and streamed outcomes stay visible after reopening a conversation. */
import assert from "node:assert/strict";
import { chromium } from "playwright";

const base = process.env.BASE_URL ?? "http://127.0.0.1:3011";
let browser;
try { browser = await chromium.launch(); }
catch (error) {
  if (process.platform !== "win32") throw error;
  browser = await chromium.launch({ channel: "msedge" });
}
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/api/pilot-session", route => route.fulfill({ json: { required: true, user: "Tester", role: "engineer" } }));
  let outcome = "no_tool_selected";
  const payload = () => ({
    turn_id: 37, batch_id: 13, question: "Find cable items", answer: "Test outcome explanation.",
    provider: "fixture", model: "fixture", session_id: "saved", sources: [], tool_calls: [],
    fallback: outcome === "no_tool_selected", response_status: outcome,
    response_reason: outcome === "clarification" ? "Which stockroom do you mean?" : "No data tool was selected.",
    intent: "lookup", staged_action: null,
  });
  await page.route("**/api/backend/**", route => {
    const path = new URL(route.request().url()).pathname.split("/api/backend/")[1];
    if (path === "health") return route.fulfill({ json: { llm_provider: "fixture", llm_model: "fixture" } });
    if (path === "pending-changes") return route.fulfill({ json: { pending: [], count: 0 } });
    if (path === "batches") return route.fulfill({ json: [{ batch_id: 13, label: "January", status: "scored" }] });
    if (path === "chat/skills") return route.fulfill({ json: { skills: [] } });
    if (path === "chat/sessions") return route.fulfill({ json: { sessions: [] } });
    if (path === "chat/sessions/saved") return route.fulfill({ json: { session_id: "saved", turns: [payload()] } });
    if (path === "chat/stream") return route.fulfill({ contentType: "application/x-ndjson", body: JSON.stringify({ type: "complete", ...payload() }) + "\n" });
    return route.fulfill({ status: 404, json: { detail: "No fixture" } });
  });
  for (const [value, label] of [["no_tool_selected", "Fallback used"], ["clarification", "Needs input"], ["no_results", "No results"]]) {
    outcome = value;
    await page.goto(base + "/chat?session=saved");
    await page.getByRole("button", { name: "View activity for message 1" }).click();
    await page.locator(".chat-activity-status").filter({ hasText: label }).waitFor();
    assert.equal(await page.locator(".chat-activity-status").innerText(), label);
    await page.reload();
    await page.getByRole("button", { name: "View activity for message 1" }).click();
    await page.locator(".chat-activity-status").filter({ hasText: label }).waitFor();
  }
  outcome = "clarification";
  const input = page.getByLabel("Ask about a recommendation", { exact: true });
  await input.fill("help with this item");
  await input.press("Enter");
  await page.getByRole("button", { name: "View activity for message 2" }).waitFor();
  await page.getByRole("button", { name: "View activity for message 2" }).click();
  await page.locator(".chat-activity-status").filter({ hasText: "Needs input" }).waitFor();
  assert.deepEqual(errors, []);
  console.log("PASS: restored outcomes, reloads, and streamed clarification");
} finally {
  await browser.close();
}
