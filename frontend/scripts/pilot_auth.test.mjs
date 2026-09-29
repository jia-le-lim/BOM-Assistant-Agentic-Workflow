import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, before, test } from "node:test";
import { hash } from "bcryptjs";
import { authRequired, signIn, signOut, getPilotSession, allowLogin, safeReturnPath, sameOrigin, SESSION_SECONDS } from "../src/lib/pilot-auth.ts";

let dir, file, fixture;
before(async () => {
  dir = await mkdtemp(join(tmpdir(), "bom-auth-unit-"));
  file = join(dir, "accounts.json");
  process.env.PILOT_AUTH_FILE = file;
  fixture = [{ user: "tester", email: "tester@example.test", hash: await hash("test-password", 4) }];
  await writeFile(file, JSON.stringify(fixture));
});
after(async () => {
  delete process.env.PILOT_AUTH_FILE;
  // Only the temporary directory created above is removed.
  if (dir) await rm(dir, { recursive: true, force: true });
});

test("email and existing username authenticate; passwords and account existence are checked", async () => {
  assert.equal(authRequired(), true);
  assert.equal((await signIn("tester", "test-password")).user, "tester");
  assert.equal((await signIn("tester@example.test", "test-password")).user, "tester");
  assert.equal(await signIn("tester", "wrong"), null);
  assert.equal(await signIn("unknown", "test-password"), null);
});

test("sessions are opaque, unforgeable and revoked by sign out", async () => {
  const { token } = await signIn("tester", "test-password");
  assert.equal(token.length, 43);
  assert.deepEqual(await getPilotSession(token), { user: "tester", role: "admin" });
  assert.equal(await getPilotSession("A".repeat(43)), null);
  assert.equal(await getPilotSession(undefined), null);
  signOut(token);
  assert.equal(await getPilotSession(token), null);
});

test("expired sessions are refused", async () => {
  const { token } = await signIn("tester", "test-password");
  const now = Date.now;
  try {
    Date.now = () => now() + (SESSION_SECONDS + 1) * 1000;
    assert.equal(await getPilotSession(token), null);
  } finally { Date.now = now; }
});

test("account removal and password rotation invalidate existing sessions", async () => {
  const original = await readFile(file, "utf8");
  const { token } = await signIn("tester", "test-password");
  try {
    await writeFile(file, JSON.stringify([{ ...fixture[0], hash: await hash("changed-password", 4) }]));
    assert.equal(await getPilotSession(token), null);
    await writeFile(file, JSON.stringify([{ ...fixture[0], user: "another" }]));
    assert.equal(await getPilotSession(token), null);
  } finally { await writeFile(file, original); }
});

test("missing and malformed auth configuration fail closed", async () => {
  delete process.env.PILOT_AUTH_FILE;
  await assert.rejects(getPilotSession(undefined));
  process.env.PILOT_AUTH_FILE = file;
  await writeFile(file, "{}");
  await assert.rejects(getPilotSession(undefined));
  await writeFile(file, JSON.stringify(fixture));
});

test("limits apply before expensive password verification", () => {
  for (let i = 0; i < 10; i++) assert.equal(allowLogin("rate-limited"), true);
  assert.equal(allowLogin("rate-limited"), false);
});

test("return paths stay local and cannot redirect back into auth APIs", () => {
  for (const path of [null, "https://example.test", "//example.test", "/\\example.test", "/login", "/api/auth/login"]) {
    assert.equal(safeReturnPath(path), "/");
  }
  assert.equal(safeReturnPath("/chat?session=test"), "/chat?session=test");
});

test("credential and logout requests require a same-origin browser request", () => {
  const request = (headers) => new Request("http://localhost:3010/api/auth/login", { headers });
  assert.equal(sameOrigin(request({ Origin: "http://localhost:3010" })), true);
  assert.equal(sameOrigin(request({ Host: "pilot.example", Origin: "https://pilot.example" })), true);
  assert.equal(sameOrigin(request({ Origin: "https://evil.example" })), false);
  assert.equal(sameOrigin(request({})), false);
  assert.equal(sameOrigin(request({ Origin: "http://localhost:3010", "Sec-Fetch-Site": "cross-site" })), false);
});
