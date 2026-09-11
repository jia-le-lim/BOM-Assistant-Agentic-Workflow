# Public pilot through the corporate proxy in Docker

The active tunnel runs in `bom-pilot-tunnel-1` on this PC. The container contains
Cloudflared 2026.9.0 and the Python proxy helper. It connects to the existing
password gateway container, which serves the Docker frontend and backend.
Existing pilot logins still apply. The previous Windows tunnel processes are stopped.

```powershell
cd C:\ProgramData\BOM-Supabase
.\Pilot.ps1 link
.\Pilot.ps1 status
.\Pilot.ps1 logs
.\Pilot.ps1 tunnel-stop
.\Pilot.ps1 start
```

The start command checks the login gate, starts the gateway and tunnel, waits for
the tunnel connection, and prints the current URL. The tunnel-stop command closes
only the public tunnel. The stop command also closes the gateway and LAN access.
The app and database remain running.

Proxy-Tunnel.ps1 remains a shortcut to these Docker operations. Its stop action
stops only the tunnel; it no longer launches Windows Python or Cloudflared processes.

The tunnel uses Docker's unless-stopped restart policy. Docker restarts it when
the engine starts unless it was manually stopped. Keep Docker Desktop running and
the PC awake and connected to the corporate network. This does not make Docker
Desktop start before Windows sign-in. Each new tunnel process generates a new
temporary hostname, so retrieve the link after restarting.

Python provisions the Quick Tunnel using verified HTTPS through
proxy-png.intel.com:912. A loopback relay passes Cloudflared's encrypted HTTP/2
connection through HTTP CONNECT to Cloudflare on TCP 7844. The origin is
http://pilot-gateway:8080; the helper refuses to publish it unless anonymous access
returns HTTP 401. Certificate verification stays enabled. Tunnel credentials stay
in memory, and the tunnel publishes no host ports.

The image runs as user 10001:10001, with a read-only filesystem, dropped Linux
capabilities, temporary runtime files, and a readiness health check. Docker
collects the logs with rotation. Use docker top bom-pilot-tunnel-1 to see Python
and Cloudflared inside the container.

Dockerfile.tunnel pins the official Cloudflared and Python base images. Installed
build sources are under runtime\pilot\tunnel-source; rebuild with Pilot.ps1 build
and apply with Pilot.ps1 start. Everyday operation does not depend on the OneDrive
checkout or Windows Python. Proxy settings remain in runtime\pilot\proxy.env.

This workaround uses Cloudflared's hidden --edge option, so revalidate it before
upgrading Cloudflared. Readiness and the URL live in /runtime inside the container;
Pilot.ps1 link also saves the URL to runtime\pilot\url.txt on Windows. Public and
browser validation reports are under runtime\pilot.

Validated on 2026-09-11: authenticated public pages, backend health, batch browsing,
pilot identity, anonymous/wrong-password denial, same/cross-origin behavior, and
clean Docker stop/start. Quick Tunnels are temporary and have no uptime guarantee.
See [Cloudflare Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/).
