/** Sidebar navigation, account actions, and responsive behavior with isolated data. */
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
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
let failLogout = true;
const sessions = [
  { session_id: "lead-times", title: "Review long lead-time parts", updated_at: new Date().toISOString(), turn_count: 4 },
  { session_id: "stock-levels", title: "Protective stock for dormant parts", updated_at: new Date().toISOString(), turn_count: 2 },
];
await page.route("**/api/pilot-session", (route) => route.fulfill({ json: { required: true, user: "Iris Renner", role: "admin" } }));
await page.route("**/api/auth/logout", (route) => route.fulfill({ status: failLogout ? 500 : 200, json: {} }));
await page.route("**/api/backend/**", (route) => {
  const path = new URL(route.request().url()).pathname.split("/api/backend/")[1];
  const reply = (json) => route.fulfill({ json });
  if (path === "health") return reply({ api_version: "test", database: "fixture", llm_provider: "test", llm_model: "NYRA" });
  if (path === "chat/sessions") return reply({ sessions });
  if (path.startsWith("chat/sessions/")) return reply({ session_id: path.split("/").at(-1), turns: [] });
  if (path === "batches") return reply([]);
  if (path === "chat/skills") return reply({ skills: [] });
  if (path === "reminders") return reply({ reminders: [], total: 0 });
  if (path === "pending-changes") return reply({ pending: [], count: 0 });
  if (path === "config/rules") return reply({ rule_version: "test-1", config: {
    long_lead_time_threshold: 60, zero_stock_risk_clt: 90, high_cost_threshold: 1500,
    low_cost_threshold: 100, recent_usage_days: 90, min_usage_months: 2,
    max_change_pct_review: 0.5, value_gate_usd: 2500, min_protective_stock: 1,
    autoclear_immaterial_usd: 0, autoclear_noop_abs: 0, autoclear_noop_rel: 0,
    autoclear_high_value_usd: 2500, autoclear_reliable: false, triage_guarded_assist_enabled: false,
  } });
  if (path === "config/part-categories" || path === "config/dormant-rules") return reply({ rules: [], confirmed: 0, pending: 0 });
  return reply({});
});

const rail = page.locator(".rail");
const nav = rail.getByRole("navigation", { name: "Sections" });
const account = rail.getByLabel("Account for Iris Renner", { exact: true });
async function noOverflow() { assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false); }
mkdirSync(".local/sidebar", { recursive: true });

try {
  await page.goto(`${base}/`);
  await account.waitFor();
  for (const name of ["Workspace", "Assistant", "Configuration"]) {
    assert.equal(await nav.getByRole("heading", { name, exact: true }).count(), 1);
  }
  assert.equal(await nav.locator('[aria-current="page"]').count(), 1);
  assert.equal(await nav.getByRole("link", { name: "Workspaces", exact: true }).getAttribute("aria-current"), "page");
  await rail.locator(".side").screenshot({ path: ".local/sidebar/dark.png" });
  await nav.getByRole("link", { name: "New workspace", exact: true }).click();
  await page.getByRole("dialog", { name: "Create a new workspace" }).waitFor();
  await page.keyboard.press("Escape");
  await nav.getByRole("link", { name: "Review settings", exact: true }).click();
  await page.waitForURL("**/config");
  assert.equal(await nav.getByRole("link", { name: "Review settings" }).getAttribute("aria-current"), "page");
  await nav.getByRole("link", { name: "Dormant rules", exact: true }).click();
  await page.waitForURL("**/config/dormant");
  assert.equal(await nav.locator('[aria-current="page"]').count(), 1);
  assert.equal(await nav.getByRole("link", { name: "Dormant rules" }).getAttribute("aria-current"), "page");
  console.log("PASS: grouped navigation, workspace shortcut, and one active destination");

  await nav.getByRole("button", { name: "Conversations", exact: true }).click();
  await rail.getByLabel("Search conversations").fill("protective");
  assert.equal(await rail.locator(".side-ask").count(), 1);
  await rail.getByRole("button", { name: /Protective stock for dormant parts/ }).click();
  await page.waitForURL("**/chat?session=stock-levels");
  await nav.getByRole("button", { name: "New conversation", exact: true }).click();
  await page.waitForURL("**/chat?new=*");
  const firstNew = page.url();
  await nav.getByRole("button", { name: "New conversation", exact: true }).click();
  await page.waitForURL((url) => url.toString() !== firstNew);
  assert.equal(new URL(page.url()).pathname, "/chat");
  console.log("PASS: conversation search, reopening history, and repeated new conversations");

  await rail.getByRole("button", { name: "Collapse sidebar", exact: true }).click();
  assert.equal(await rail.locator(".side").evaluate((element) => Math.round(element.getBoundingClientRect().width)), 60);
  await page.reload();
  await account.waitFor();
  assert.equal(await page.locator("html").getAttribute("data-rail"), "collapsed");
  assert.equal(await nav.getByRole("link", { name: "Review settings", exact: true }).isVisible(), true);
  await rail.locator(".side").screenshot({ path: ".local/sidebar/collapsed.png" });
  await account.click();
  const menuBox = await rail.locator(".side-account-menu").boundingBox();
  assert.ok(menuBox.x > 60 && menuBox.width >= 200, "Collapsed account menu opens beside the rail");
  await page.keyboard.press("Escape");
  assert.equal(await rail.locator(".side-account").evaluate((element) => element.open), false);
  await nav.getByRole("button", { name: "Conversations", exact: true }).click();
  assert.equal(await page.locator("html").getAttribute("data-rail"), "expanded");
  assert.equal(await rail.getByLabel("Search conversations").isVisible(), true);
  console.log("PASS: persistent collapse, accessible icon links, account popover, and history expansion");

  await account.click();
  await rail.getByRole("button", { name: "Sign out", exact: true }).click();
  await rail.getByRole("alert").waitFor();
  assert.match(await rail.getByRole("alert").textContent(), /Could not sign out/);
  await page.keyboard.press("Escape");
  assert.equal(await account.evaluate((element) => element === document.activeElement), true);
  console.log("PASS: account menu preserves sign-out error recovery and Escape returns focus");

  for (const theme of ["light", "dark"]) {
    await page.emulateMedia({ colorScheme: theme });
    await page.setViewportSize({ width: 1280, height: 720 });
    await nav.getByRole("button", { name: "Conversations", exact: true }).click();
    await rail.locator(".side").screenshot({ path: `.local/sidebar/${theme}-720.png` });
    await noOverflow();
    await page.setViewportSize({ width: 390, height: 700 });
    await page.getByRole("button", { name: "Open navigation", exact: true }).click();
    const drawer = page.getByRole("dialog", { name: "Workspace", exact: true });
    await drawer.waitFor();
    await page.waitForFunction(() => document.querySelector(".drawer")?.contains(document.activeElement));
    await page.keyboard.press("Shift+Tab");
    assert.equal(await drawer.locator("summary").evaluate((element) => element === document.activeElement), true);
    await page.keyboard.press("Tab");
    assert.equal(await drawer.getByRole("link", { name: "BOM Review home", exact: true }).evaluate((element) => element === document.activeElement), true);
    assert.equal(await page.locator(".shell-main").getAttribute("inert"), "");
    await noOverflow();
    await page.screenshot({ path: `.local/sidebar/${theme}-mobile.png` });
    await drawer.getByRole("link", { name: "Review settings", exact: true }).click();
    await page.waitForURL("**/config");
    assert.equal(await drawer.count(), 0);
    await page.getByRole("button", { name: "Open navigation", exact: true }).click();
    await drawer.getByRole("button", { name: "Close navigation", exact: true }).click();
    assert.equal(await page.getByRole("button", { name: "Open navigation", exact: true }).evaluate((element) => element === document.activeElement), true);
  }
  console.log("PASS: both themes, mobile drawer navigation, focus containment, and no horizontal overflow");
  await page.setViewportSize({ width: 1440, height: 900 });
  await account.click();
  failLogout = false;
  await rail.getByRole("button", { name: "Sign out", exact: true }).click();
  await page.waitForURL("**/login");
  assert.deepEqual(errors, []);
  console.log("PASS: successful sign-out reaches login; no browser runtime errors");
} finally { await browser.close(); }
