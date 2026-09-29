import { NextRequest, NextResponse } from "next/server";
import { allowLogin, sameOrigin, SESSION_COOKIE, SESSION_SECONDS, signIn, signOut } from "@/lib/pilot-auth";

export const runtime = "nodejs";

export async function POST(request: NextRequest) {
  const reply = (detail: string, status: number) => NextResponse.json({ detail }, {
    status, headers: { "Cache-Control": "no-store" },
  });
  if (!sameOrigin(request)) return reply("Cross-origin requests are not allowed.", 403);
  if (!request.headers.get("content-type")?.startsWith("application/json")) return reply("Expected a JSON request.", 415);
  let input;
  try {
    // Read with a hard limit even when Content-Length is absent or forged.
    const reader = request.body?.getReader();
    if (!reader) return reply("Enter your account and password.", 400);
    const chunks: Uint8Array[] = [];
    let size = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 4096) { await reader.cancel(); return reply("Request is too large.", 413); }
      chunks.push(value);
    }
    input = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch { return reply("Enter your account and password.", 400); }
  if (!input || typeof input.identifier !== "string" || typeof input.password !== "string"
      || !input.identifier.trim() || input.identifier.length > 254 || !input.password
      || Buffer.byteLength(input.password, "utf8") > 72) {
    return reply("Enter a valid account and password.", 400);
  }
  const identifier = input.identifier.trim().toLowerCase();
  if (!allowLogin(identifier)) return reply("Too many attempts. Try again in 15 minutes.", 429);
  try {
    const session = await signIn(identifier, input.password);
    if (!session) return reply("The account or password is incorrect. Please try again.", 401);
    signOut(request.cookies.get(SESSION_COOKIE)?.value);
    const response = NextResponse.json({ user: session.user }, { headers: { "Cache-Control": "no-store" } });
    response.cookies.set(SESSION_COOKIE, session.token, {
      httpOnly: true, sameSite: "lax", path: "/", maxAge: SESSION_SECONDS,
      // The Origin is checked against Host above. HTTPS tunnel visitors receive Secure cookies.
      secure: request.headers.get("origin")?.startsWith("https://") ?? false,
    });
    return response;
  } catch {
    return reply("Sign-in is temporarily unavailable. Please contact the project owner.", 503);
  }
}
