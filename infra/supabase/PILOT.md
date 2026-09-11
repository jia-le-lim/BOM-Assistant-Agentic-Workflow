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
to every frontend page, asset and /api/backend/ request. Private account details
are in pilot-accounts.local.json; share them separately with invited testers.

The pilot uses the live migrated BOM database and existing role selector,
including admin actions. Authorized testers can read, export and modify pilot data.
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

Quick Tunnels are temporary and have no uptime guarantee. Cloudflare documents
a 200 concurrent-request limit and no SSE support. The app uses NDJSON for chat;
the Docker migration checks cover navigation and read-only API access, not every
chat or write workflow. See [Cloudflare Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/).
