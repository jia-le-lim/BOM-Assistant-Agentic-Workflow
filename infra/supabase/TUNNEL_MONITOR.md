# Cloudflare tunnel monitor

The Windows monitor checks the current BOM Assistant Quick Tunnel and emails a
verified replacement link through classic Outlook. Open its local status page at
http://127.0.0.1:13011. The page remains available when the tunnel is down.

## Install or update

From the repository root, with the pilot already installed:

```powershell
./infra/supabase/Install-TunnelMonitor.ps1 `
    -DeploymentRoot C:/ProgramData/BOM-Supabase `
    -PythonExe 'C:/Program Files/Python313/pythonw.exe' `
    -Recipient 'jia.le.lim@intel.com' `
    -Sender 'jia.le.lim@intel.com' -Delivery outlook -Start
```

Python 3.11 or newer is required; there are no Python package dependencies.
Installation copies the monitor and pilot commands into the deployment folder and
registers `BOM-Cloudflare-Monitor` for the current Windows user's sign-in. It runs
hidden with that user's normal permissions. Updates preserve existing email
settings and the recovery pause state. Everyday operation uses the installed
files, without relying on the repository checkout.

Keep this Windows user signed in, Docker Desktop running, and the PC awake and
connected. Classic Outlook must have the configured sending mailbox signed in.
The monitor selects that exact account using Outlook's
[SendUsingAccount property](https://learn.microsoft.com/en-us/office/vba/api/outlook.mailitem.sendusingaccount).
It does not store an Outlook password or read inbox messages. Outlook or corporate
policy may require a user interaction for automation; its security settings are
left unchanged. See Microsoft's
[Outlook automation security documentation](https://learn.microsoft.com/en-us/office/vba/outlook/how-to/security/security-behavior-of-the-outlook-object-model).

## Checks and recovery

- Each cycle reads the URL from the currently running `bom-pilot-tunnel-1`
  container, rather than an old log entry. The next cycle starts 30 seconds after
  the previous cycle finishes.
- HTTPS checks verify that the public login page loads and anonymous backend
  access still requires authentication. HTTP requests are used instead of ICMP.
- Three consecutive failed checks trigger a restart of that tunnel container.
  The existing tunnel helper creates the replacement Quick Tunnel URL.
- New URLs receive a 90-second connection grace period. Recovery attempts have
  a five-minute cooldown, increasing to at most 30 minutes during repeated failure.
- Local application failures, unavailable Docker, and general Cloudflare/proxy
  connectivity failures are reported without repeatedly rotating the URL. The
  monitor does not start Docker or rebuild the application.
- A URL is published and queued for email only after verification. This includes
  the first verified URL after installation and subsequent replacement URLs.
- Failed email submissions retry with backoff. Only the latest verified URL
  remains queued; alerts for links that are currently failing are withheld.

The status page reports Outlook submission, which does not confirm arrival in
the inbox. Outlook can queue mail while disconnected. Alerts contain the public
link and verification time, with no passwords or BOM contents.

Cloudflare Quick Tunnels are temporary and have
[no uptime guarantee](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/).
Recovery is available only while this PC and its dependencies are running.

## Everyday commands

```powershell
cd C:/ProgramData/BOM-Supabase
./Pilot.ps1 monitor-status  # Current JSON status
./Pilot.ps1 monitor-stop    # Pause checks/recovery; leave the tunnel open
./Pilot.ps1 monitor-start   # Resume checks/recovery and start the monitor task
./Pilot.ps1 tunnel-stop     # Close the tunnel and pause recovery
./Pilot.ps1 start           # Start the pilot and enable recovery
```

Use `Pilot.ps1 tunnel-stop` for an intentional shutdown. A direct `docker stop`
of the tunnel while recovery is enabled is treated as a failure and recovered.
The read-only status page stays available when checks are paused. Its listener
binds only to `127.0.0.1` and has no restart or configuration HTTP endpoints.

Installed state is under `C:/ProgramData/BOM-Supabase/runtime/pilot/monitor`:

| File | Purpose |
| --- | --- |
| `email.local.json` | Private delivery settings; Outlook uses enabled, delivery, sender and recipient |
| `control.json` | Recovery enabled/paused flag |
| `status.json` | Latest status, recent events, cooldowns and pending email |
| `monitor.lock` | Prevents duplicate monitors for the same deployment |

The latest verified URL is also saved to `runtime/pilot/url.txt`. Email settings
are reread on each eligible notification attempt. Do not commit local settings.
An optional SMTP adapter supports STARTTLS or TLS when an approved relay becomes
available; the installed configuration uses Outlook.

## Troubleshooting and validation

If email status says `retrying`, open classic Outlook, check the configured
mailbox is signed in and can send mail, and handle any Outlook prompt. The queued
link retries automatically. If the status page is unavailable, inspect the
`BOM-Cloudflare-Monitor` task in Task Scheduler or start it with `monitor-start`.

For foreground diagnostics, first stop the scheduled task to avoid duplicate
processes, then run:

```powershell
Stop-ScheduledTask -TaskName BOM-Cloudflare-Monitor
& 'C:/Program Files/Python313/python.exe' C:/ProgramData/BOM-Supabase/tunnel_monitor.py --once
Start-ScheduledTask -TaskName BOM-Cloudflare-Monitor
```

`--once` performs a real monitoring cycle, including eligible recovery and email.
Offline tests never contact Docker, create tunnels or send mail:

```powershell
python -m unittest discover -s infra/supabase -p test_tunnel_monitor.py -v
```
