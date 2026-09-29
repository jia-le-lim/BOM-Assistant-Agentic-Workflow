/**
 * BFF proxy to the FastAPI backend.
 *
 * Everything the browser calls goes through here, which means:
 *   - no CORS configuration is needed (same-origin from the browser's view)
 *   - the verified session supplies identity and Administrator access.
 *     Browser identity and role headers cannot override an authenticated session.
 *     Header-driven identities remain available only in unauthenticated local development.
 */

import { NextRequest } from "next/server";
import { authRequired, getPilotSession, sameOrigin, SESSION_COOKIE } from "@/lib/pilot-auth";

const BACKEND = process.env.BACKEND_URL ?? "http://127.0.0.1:8012";

async function forward(req: NextRequest, path: string[]) {
  let user = req.headers.get("x-user") ?? "anonymous";
  let role = req.headers.get("x-role") ?? "viewer";
  if (authRequired()) {
    try {
      const session = await getPilotSession(req.cookies.get(SESSION_COOKIE)?.value);
      if (!session) return Response.json({ detail: "Sign in to continue." }, { status: 401, headers: { "Cache-Control": "no-store" } });
      user = session.user;
      role = session.role;
    } catch {
      return Response.json({ detail: "Sign-in is temporarily unavailable." }, { status: 503 });
    }
    if (!["GET", "HEAD"].includes(req.method) && !sameOrigin(req)) {
      return Response.json({ detail: "Cross-origin requests are not allowed." }, { status: 403 });
    }
  }
  const target = new URL(`${BACKEND}/${path.join("/")}`);
  req.nextUrl.searchParams.forEach((v, k) => target.searchParams.append(k, v));

  const headers = new Headers();
  headers.set("X-User", user);
  headers.set("X-Role", role);
  const ct = req.headers.get("content-type");
  if (ct && !ct.startsWith("multipart/form-data")) headers.set("Content-Type", ct);

  const init: RequestInit = { method: req.method, headers, cache: "no-store" };
  if (!["GET", "HEAD"].includes(req.method)) {
    init.body = ct?.startsWith("multipart/form-data")
      ? await req.formData()
      : await req.text();
  }

  try {
    const res = await fetch(target, init);
    const streaming = res.headers.get("content-type")?.includes("application/x-ndjson");
    const body = streaming ? res.body : await res.arrayBuffer();
    const out = new Headers();
    // By prefix, not by name. The backend's X- headers are export counts the
    // console renders; an allowlist of literal names has to be edited in
    // lockstep with every new one, and when it is not, the count silently
    // arrives as null rather than failing.
    for (const [k, v] of res.headers) {
      if (k === "content-type" || k === "content-disposition" || k === "cache-control"
          || k.startsWith("x-")) {
        out.set(k, v);
      }
    }
    out.set("Cache-Control", "private, no-store");
    return new Response(body, { status: res.status, headers: out });
  } catch {
    return Response.json(
      { detail: `Backend unreachable at ${BACKEND}. Start it with: ` +
                `make backend BACKEND_PORT=${new URL(BACKEND).port || "8012"}` },
      { status: 503 },
    );
  }
}

type Ctx = { params: Promise<{ path: string[] }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path);
}
export async function POST(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path);
}
export async function DELETE(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path);
}
