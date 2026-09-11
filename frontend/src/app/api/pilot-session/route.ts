// The pilot gateway overwrites this header after authenticating the user.
// Direct localhost development retains the existing demo identities.
export async function GET(request: Request) {
  return Response.json(
    { user: request.headers.get("x-pilot-user") },
    { headers: { "Cache-Control": "no-store" } },
  );
}
