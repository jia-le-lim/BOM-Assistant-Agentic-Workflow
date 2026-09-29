/** Server-only pilot authentication. Opaque sessions live in this single Node process.
 * A restart signs everyone out. The private account file contains bcrypt hashes only.
 */
import { createHash, randomBytes } from "node:crypto";
import { readFile } from "node:fs/promises";
import { compare } from "bcryptjs";

export const SESSION_COOKIE = "bom-pilot-session";
export const SESSION_SECONDS = 8 * 60 * 60;

interface Account { user: string; hash: string; email?: string; name?: string }
interface PilotSession { user: string; hash: string; expires: number }
interface Attempt { count: number; expires: number }
const globals = globalThis as typeof globalThis & {
  bomPilotAuth?: { sessions: Map<string, PilotSession>; attempts: Map<string, Attempt> };
};
const state = globals.bomPilotAuth ??= { sessions: new Map(), attempts: new Map() };

export function authRequired() {
  return process.env.PILOT_AUTH_REQUIRED === "1" || Boolean(process.env.PILOT_AUTH_FILE);
}

async function accounts(): Promise<Account[]> {
  const path = process.env.PILOT_AUTH_FILE;
  if (!path) throw new Error("Pilot authentication is not configured.");
  const value: unknown = JSON.parse(await readFile(path, "utf8"));
  if (!Array.isArray(value) || !value.length || !value.every((a) =>
    a && typeof a.user === "string" && /^[a-z0-9][a-z0-9-]{0,39}$/.test(a.user)
    && typeof a.hash === "string" && /^\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}$/.test(a.hash)
    && (a.email === undefined || typeof a.email === "string")
    && (a.name === undefined || typeof a.name === "string"))) {
    throw new Error("Invalid pilot authentication configuration.");
  }
  return value;
}

const digest = (token: string) => createHash("sha256").update(token).digest("hex");

function prune() {
  const now = Date.now();
  for (const [key, value] of state.sessions) if (value.expires <= now) state.sessions.delete(key);
  for (const [key, value] of state.attempts) if (value.expires <= now) state.attempts.delete(key);
}

/** Account-based limits also apply when a caller forges forwarding headers. */
export function allowLogin(identifier: string) {
  prune();
  for (const [key, limit] of [["global", 100], [`account:${identifier}`, 10]] as const) {
    const attempt = state.attempts.get(key);
    if (attempt && attempt.count >= limit) return false;
  }
  for (const key of ["global", `account:${identifier}`]) {
    const attempt = state.attempts.get(key) ?? { count: 0, expires: Date.now() + 15 * 60_000 };
    attempt.count++;
    state.attempts.set(key, attempt);
  }
  return true;
}

export async function signIn(identifier: string, password: string) {
  const list = await accounts();
  const account = list.find((a) => a.user === identifier || a.email?.toLowerCase() === identifier);
  // Unknown accounts still perform bcrypt work and receive the same error.
  const valid = await compare(password, account?.hash ?? list[0].hash);
  if (!valid || !account) return null;
  prune();
  state.attempts.delete(`account:${identifier}`);
  const token = randomBytes(32).toString("base64url");
  state.sessions.set(digest(token), {
    user: account.user, hash: account.hash, expires: Date.now() + SESSION_SECONDS * 1000,
  });
  return { token, user: account.user };
}

export async function getPilotSession(token: string | undefined) {
  // Configuration must be valid even when no cookie is supplied: the gateway fails closed.
  const list = await accounts();
  prune();
  if (!token || !/^[A-Za-z0-9_-]{43}$/.test(token)) return null;
  const key = digest(token);
  const session = state.sessions.get(key);
  if (!session) return null;
  const account = list.find((a) => a.user === session.user && a.hash === session.hash);
  if (!account) { state.sessions.delete(key); return null; }
  return { user: account.user, role: "admin" as const };
}

export function signOut(token: string | undefined) {
  if (token) state.sessions.delete(digest(token));
}

export function sameOrigin(request: Request) {
  if (request.headers.get("sec-fetch-site") === "cross-site") return false;
  const origin = request.headers.get("origin");
  const host = request.headers.get("host") ?? new URL(request.url).host;
  return origin === `https://${host}` || origin === `http://${host}`;
}

export function safeReturnPath(value: string | null) {
  if (!value || !value.startsWith("/") || value.startsWith("//") || /[\\\x00-\x20]/.test(value)) return "/";
  const path = new URL(value, "http://localhost");
  return path.pathname === "/login" || path.pathname.startsWith("/api/") ? "/" : value;
}
