/**
 * BFF proxy to the FastAPI backend.
 *
 * Everything the browser calls goes through here, which means:
 *   - no CORS configuration is needed (same-origin from the browser's view)
 *   - identity is injected server-side, which is where Entra ID will plug in.
 *     Today the role arrives from the client role-switcher; in production this
 *     route reads the authenticated session instead and the client cannot
 *     choose its own role.
 */

import { NextRequest } from "next/server";

const BACKEND = process.env.BACKEND_URL ?? "http://127.0.0.1:8011";

async function forward(req: NextRequest, path: string[]) {
  const target = new URL(`${BACKEND}/${path.join("/")}`);
  req.nextUrl.searchParams.forEach((v, k) => target.searchParams.append(k, v));

  const headers = new Headers();
  // Identity stub -- see note above. Replace with server-derived session.
  headers.set("X-User", req.headers.get("x-user") ?? "anonymous");
  headers.set("X-Role", req.headers.get("x-role") ?? "viewer");
  const ct = req.headers.get("content-type");
  if (ct && !ct.startsWith("multipart/form-data")) headers.set("Content-Type", ct);

  const init: RequestInit = { method: req.method, headers };
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
    return new Response(body, { status: res.status, headers: out });
  } catch {
    return Response.json(
      { detail: `Backend unreachable at ${BACKEND}. Start it with: ` +
                `.venv\\Scripts\\python.exe -m uvicorn app.main:app --app-dir backend --port 8011` },
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
