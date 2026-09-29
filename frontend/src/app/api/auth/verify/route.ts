import { NextRequest } from "next/server";
import { getPilotSession, safeReturnPath, SESSION_COOKIE } from "@/lib/pilot-auth";

/** Caddy forward_auth always requires a session, including in demo mode. */
export async function GET(request: NextRequest) {
  const headers = { "Cache-Control": "no-store" };
  try {
    const session = await getPilotSession(request.cookies.get(SESSION_COOKIE)?.value);
    if (session) return new Response(null, { status: 204, headers: { ...headers, "X-Pilot-User": session.user } });
    const uri = request.headers.get("x-forwarded-uri") ?? "/api/auth/verify";
    if (uri.startsWith("/api/")) return Response.json({ detail: "Sign in to continue." }, { status: 401, headers });
    return new Response(null, { status: 303, headers: {
      ...headers, Location: `/login?next=${encodeURIComponent(safeReturnPath(uri))}`,
    } });
  } catch {
    return Response.json({ detail: "Sign-in is temporarily unavailable." }, { status: 503, headers });
  }
}
