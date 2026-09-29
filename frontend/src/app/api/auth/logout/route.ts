import { NextRequest, NextResponse } from "next/server";
import { sameOrigin, SESSION_COOKIE, signOut } from "@/lib/pilot-auth";

export async function POST(request: NextRequest) {
  if (!sameOrigin(request)) return NextResponse.json({ detail: "Cross-origin requests are not allowed." }, { status: 403 });
  signOut(request.cookies.get(SESSION_COOKIE)?.value);
  const response = NextResponse.json({ ok: true }, { headers: { "Cache-Control": "no-store" } });
  response.cookies.set(SESSION_COOKIE, "", {
    httpOnly: true, sameSite: "lax", path: "/", maxAge: 0,
    secure: request.headers.get("origin")?.startsWith("https://") ?? false,
  });
  return response;
}
