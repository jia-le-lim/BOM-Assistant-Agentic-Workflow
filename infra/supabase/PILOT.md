# Cloudflare pilot sharing

Public sharing runs in Docker on the shared PC at C:\ProgramData\BOM-Supabase.
The bom-pilot-tunnel-1 container contains Cloudflared and its corporate proxy
helper. The Windows tunnel processes used during initial setup are stopped.
See [PROXY_TUNNEL.md](PROXY_TUNNEL.md) for implementation and operating details.

## Operate the pilot

```powershell
cd C:\ProgramData\BOM-Supabase
.\Pilot.ps1 start
.\Pilot.ps1 link
.\Pilot.ps1 status
.\Pilot.ps1 logs
.\Pilot.ps1 tunnel-stop
.\Pilot.ps1 stop
```

The start command checks that the password gateway denies anonymous access,
starts the tunnel, and prints the link after connection. The tunnel-stop command
closes only the public tunnel; stop also closes the gateway and its LAN listener.
Neither stops the app or database. Use local-start for the gateway only. The build
command rebuilds the image from installed sources, and start applies the image.

The tunnel restarts with Docker unless manually stopped. Quick Tunnel URLs change
when the process restarts; retrieve the current URL with the link command. Docker
Desktop, the corporate network connection, and the app must remain available.

## Access and scope

Internet browser -> Cloudflare HTTPS -> corporate proxy -> Docker tunnel ->
password gateway -> frontend -> backend. Existing individual pilot logins apply
to workspace pages and /api/backend/ requests. The login form and its static
assets are public. Private account details
are in pilot-accounts.local.json; share them separately with invited testers.

The pilot uses the live migrated BOM database. Every existing login has
Administrator access: authorized testers can read, export and modify pilot data.
The gateway login does not replace Entra ID/SSO or finer role permissions. Studio,
PostgreSQL and the direct backend port are not tunnel origins.

The initial direct Cloudflare TCP/UDP attempt failed. The working container uses
HTTP CONNECT through the corporate proxy with certificate verification enabled.
Older diagnostics remain in runtime\pilot\connection-diagnostics.log.

## Verification

The container health check verifies its Cloudflared connection. Public HTTP checks
verify authenticated pages, health, batch listing, pilot identity, login denials
and same/cross-origin behavior. Validation reports are stored in runtime\pilot.
The check_pilot.py script remains available for local gateway checks.

## Custom sign-in form

The `/login` page uses an email or pilot username step followed by a password
step that recalls the selected account. Existing pilot passwords still work.
The sidebar's Sign out button revokes the session; switching accounts no longer
requires a new private browser window. “Remember my account” saves only the
identifier on this device, never the password.

Caddy uses `forward_auth` against `/api/auth/verify`. Unauthenticated workspace
pages redirect to `/login`; API requests return 401 without `WWW-Authenticate`.
The frontend also verifies the cookie before forwarding API requests and derives
`X-User` and the fixed `admin` role from the session. The sidebar displays one
Administrator identity. Browser headers and saved role selections cannot change
authenticated access. Existing logins and the two-person approval workflow remain.

`pilot_users.py` renders `runtime/pilot/auth.json` from the existing private
account file. Only usernames, optional email aliases, and bcrypt hashes enter
this read-only frontend mount. To enable email login, add an `email` field to
the intended account in `pilot-accounts.local.json`, then rerun the renderer.
Do not invent aliases or distribute the hash file. Keep the deployment directory
restricted to its administrators.

Sessions are random opaque cookies, HttpOnly and SameSite=Lax, valid for eight
hours. HTTPS connections receive Secure cookies. They are held by the single
frontend Node process: restarting it signs everyone out. Logout, account removal,
and password changes revoke sessions. A multi-instance deployment would need a
shared session store. Failed attempts are limited per account and globally.

Apply this change to the existing deployment as a coordinated frontend and
gateway update. These commands do not start a public tunnel:

```powershell
# From the repository, with access to the existing deployment and Docker:
py -3.13 infra/supabase/pilot_users.py
py -3.13 infra/supabase/install_app.py
& C:/ProgramData/BOM-Supabase/Compose.ps1 build
& C:/ProgramData/BOM-Supabase/Compose.ps1 update-app
& C:/ProgramData/BOM-Supabase/Pilot.ps1 local-start
py -3.13 infra/supabase/check_pilot.py
# If this deployment uses the optional tunnel, rebuild its staged startup check:
& C:/ProgramData/BOM-Supabase/Pilot.ps1 build
```

The renderer also stages the updated gateway check scripts and existing tunnel
source. A running tunnel may keep serving through the gateway; its next `start`
must use the rebuilt image. Recreating a Quick Tunnel changes its public URL.

Prepare `auth.json` before recreating the frontend: its bind mount deliberately
fails if the file is missing. Both the gateway and direct frontend API fail
closed when authentication is required but its configuration is unavailable.
The frontend health check verifies a configured authentication service; the
backend retains its separate health check. Tunnel startup checks the protected
API endpoint, since the public login page now returns 200.

Quick Tunnels are temporary and have no uptime guarantee. Cloudflare documents
a 200 concurrent-request limit and no SSE support. The app uses NDJSON for chat;
the Docker migration checks cover navigation and read-only API access, not every
chat or write workflow. See [Cloudflare Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/).
