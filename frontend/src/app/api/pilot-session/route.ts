import { NextRequest } from "next/server";
import { authRequired, getPilotSession, SESSION_COOKIE } from "@/lib/pilot-auth";

export async function GET(request: NextRequest) {
  const headers = { "Cache-Control": "no-store" };
  if (!authRequired()) return Response.json({ user: "pilot", role: "admin", required: false }, { headers });
  try {
    const session = await getPilotSession(request.cookies.get(SESSION_COOKIE)?.value);
    return Response.json({ user: session?.user ?? null, role: session?.role ?? null, required: true }, { status: session ? 200 : 401, headers });
  } catch {
    return Response.json({ user: null, required: true }, { status: 503, headers });
  }
}
