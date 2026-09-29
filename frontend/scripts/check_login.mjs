/** Real production-server auth checks with private temporary accounts and a fake backend. */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir, writeFile, appendFile, unlink } from "node:fs/promises";
import { createServer } from "node:http";
import { resolve } from "node:path";
import { hash } from "bcryptjs";
import { chromium } from "playwright";

const artifacts = resolve(".auth-check");
await mkdir(artifacts, { recursive: true });
const accountFile = resolve(artifacts, "accounts.json");
await writeFile(accountFile, JSON.stringify([{ user: "tester", email: "tester@example.test", hash: await hash("test-password", 4) }]));
const backend = createServer((request, response) => {
  response.setHeader("Content-Type", "application/json");
  if (request.url === "/identity") return response.end(JSON.stringify({ user: request.headers["x-user"], role: request.headers["x-role"] }));
  if (request.url === "/admin-only") {
    response.statusCode = request.headers["x-role"] === "admin" ? 200 : 403;
    return response.end(JSON.stringify({ allowed: response.statusCode === 200 }));
  }
  if (request.url === "/health") return response.end(JSON.stringify({ api_version: "test", database: "fixture", llm_provider: "echo", llm_model: "Fixture" }));
  response.end(JSON.stringify(request.url?.startsWith("/chat/sessions") ? { sessions: [] } : []));
});
await new Promise((done) => backend.listen(0, "127.0.0.1", done));
const port = Number(process.env.AUTH_TEST_PORT ?? 3120);
const base = `http://127.0.0.1:${port}`;
const server = spawn(process.execPath, ["node_modules/next/dist/bin/next", "start", "--hostname", "127.0.0.1", "--port", String(port)], {
  windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
  env: { ...process.env, NODE_ENV: "production", PILOT_AUTH_REQUIRED: "1", PILOT_AUTH_FILE: accountFile,
    BACKEND_URL: `http://127.0.0.1:${backend.address().port}` },
});
for (const stream of [server.stdout, server.stderr]) stream.on("data", (data) => {
  void appendFile(resolve(artifacts, "server.log"), data);
});
let browser;
try {
  let ready = false;
  for (let attempt = 0; attempt < 80; attempt++) {
    if (server.exitCode !== null) throw new Error("Test frontend exited. Check .auth-check/server.log.");
    try { ready = (await fetch(`${base}/api/pilot-session`)).status === 401; } catch { /* wait for startup */ }
    if (ready) break;
    await new Promise((done) => setTimeout(done, 250));
  }
  assert.ok(ready, "Production frontend started with auth enabled");
  try { browser = await chromium.launch(); }
  catch (error) {
    if (process.platform !== "win32") throw error;
    browser = await chromium.launch({ channel: "msedge" });
  }
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, colorScheme: "dark" });
  const api = context.request;
  const anonymous = await api.get(`${base}/api/backend/health`, { headers: { "X-User": "root", "X-Role": "admin", "X-Pilot-User": "root" } });
  assert.equal(anonymous.status(), 401);
  assert.equal(anonymous.headers()["www-authenticate"], undefined);
  assert.equal((await api.get(`${base}/api/pilot-session`, { headers: { "X-Pilot-User": "root" } })).status(), 401);
  const redirect = await api.get(`${base}/api/auth/verify`, { headers: { "X-Forwarded-Uri": "/chat?new=1" }, maxRedirects: 0 });
  assert.equal(redirect.status(), 303);
  assert.equal(redirect.headers().location, "/login?next=%2Fchat%3Fnew%3D1");
  const credentials = { identifier: "tester@example.test", password: "test-password" };
  assert.equal((await api.post(`${base}/api/auth/login`, { data: credentials })).status(), 403);
  assert.equal((await api.post(`${base}/api/auth/login`, { data: credentials, headers: { Origin: "https://unrelated.example" } })).status(), 403);
  assert.equal((await api.post(`${base}/api/auth/login`, { data: null, headers: { Origin: base } })).status(), 400);
  assert.equal((await api.post(`${base}/api/auth/login`, { data: "plain text", headers: { Origin: base, "Content-Type": "text/plain" } })).status(), 415);
  assert.equal((await api.post(`${base}/api/auth/login`, { data: { ...credentials, password: "x".repeat(5000) }, headers: { Origin: base } })).status(), 413);

  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${base}/login?next=${encodeURIComponent("/?from=login")}`);
  assert.equal(await page.locator(".shell, .rail, .topbar").count(), 0);
  await page.getByLabel("Email or username", { exact: true }).fill("tester@example.test");
  await page.getByLabel("Remember my account on this device").check();
  await page.screenshot({ path: resolve(artifacts, "login-dark.png"), fullPage: true });
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  assert.equal(await page.locator(".auth-account strong").textContent(), "tester@example.test");
  await page.getByLabel("Password", { exact: true }).fill("wrong-password");
  await page.getByRole("button", { name: "Show password", exact: true }).click();
  assert.equal(await page.getByLabel("Password", { exact: true }).getAttribute("type"), "text");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.locator("#login-error").waitFor();
  assert.match(await page.locator("#login-error").textContent(), /incorrect/);
  assert.equal(await page.getByLabel("Password", { exact: true }).inputValue(), "");
  await page.getByRole("button", { name: "Change", exact: true }).click();
  assert.equal(await page.getByLabel("Email or username", { exact: true }).inputValue(), "tester@example.test");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.getByRole("button", { name: "Forgot password?", exact: true }).click();
  assert.match(await page.locator("#password-help").textContent(), /project owner/);
  await page.screenshot({ path: resolve(artifacts, "password-dark.png"), fullPage: true });
  await page.getByLabel("Password", { exact: true }).fill("test-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.waitForURL(`${base}/?from=login`);
  const cookie = (await context.cookies()).find((value) => value.name === "bom-pilot-session");
  assert.ok(cookie?.httpOnly);
  assert.equal(cookie.sameSite, "Lax");
  assert.equal((await api.get(`${base}/api/auth/verify`)).status(), 204);
  assert.deepEqual(await (await api.get(`${base}/api/pilot-session`)).json(), { user: "tester", role: "admin", required: true });
  for (const role of [undefined, "viewer", "engineer", "admin", "invalid-role"]) {
    const headers = { "X-User": "root", ...(role ? { "X-Role": role } : {}) };
    const identity = await api.get(`${base}/api/backend/identity`, { headers });
    assert.deepEqual(await identity.json(), { user: "tester", role: "admin" });
    assert.equal((await api.post(`${base}/api/backend/admin-only`, { headers: { ...headers, Origin: base } })).status(), 200);
  }
  await page.evaluate(() => localStorage.setItem("bom-session", JSON.stringify({ user: "old-user", role: "viewer" })));
  await page.reload();
  await page.locator(".side-username").filter({ hasText: "tester" }).waitFor();
  assert.equal(await page.locator(".side-user select").count(), 0);
  assert.equal(await page.locator(".side-access").textContent(), "Administrator");
  assert.equal(await page.getByRole("button", { name: "Upload & score", exact: true }).isEnabled(), true);
  assert.equal(await page.evaluate(() => localStorage.getItem("bom-session")), null);
  await page.locator(".side-foot").screenshot({ path: resolve(artifacts, "administrator-sidebar.png") });
  assert.equal((await api.post(`${base}/api/auth/logout`, { headers: { Origin: "https://unrelated.example" } })).status(), 403);
  await page.getByLabel("Account for tester", { exact: true }).click();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await page.waitForURL(`${base}/login`);
  await page.waitForFunction(() => document.querySelector("#login-account")?.value === "tester@example.test");
  const revoked = await api.get(`${base}/api/auth/verify`, { headers: { Cookie: `bom-pilot-session=${cookie.value}` } });
  assert.equal(revoked.status(), 401);
  assert.equal((await api.get(`${base}/api/backend/health`)).status(), 401);
  assert.ok(!(await page.evaluate(() => JSON.stringify(localStorage))).includes("test-password"));

  for (const colorScheme of ["dark", "light"]) {
    await page.emulateMedia({ colorScheme, reducedMotion: "reduce" });
    await page.setViewportSize({ width: 375, height: 812 });
    await page.screenshot({ path: resolve(artifacts, `login-mobile-${colorScheme}.png`), fullPage: true });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "No mobile overflow");
  }
  await page.goto(`${base}/login?next=${encodeURIComponent("//unrelated.example")}`);
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.getByLabel("Password", { exact: true }).fill("test-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.waitForURL(`${base}/`);
  assert.deepEqual(errors, []);
  console.log("PASS: real login/logout, fixed Administrator access, stale roles ignored, server-derived identity and role, cookie protection, gateway checks, CSRF, redirects, dark/light mobile layouts.");
} finally {
  await browser?.close();
  server.kill();
  backend.close();
  await unlink(accountFile);
}
