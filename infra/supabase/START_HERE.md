# Start the BOM Assistant on this shared PC

For colleagues on the office network, use **http://10.138.215.247:13010** with
an individual pilot login (`tcb-1` or `epoxy-1`). Another-PC access is confirmed.
See [INTERNAL_ACCESS.md](INTERNAL_ACCESS.md) for passwords, startup and network
troubleshooting. For the public link, run `C:\ProgramData\BOM-Supabase\Pilot.ps1 link`
and use the same pilot login. Cloudflared and its corporate proxy helper now run
in Docker; see [PROXY_TUNNEL.md](PROXY_TUNNEL.md) for start/stop instructions.
The [tunnel monitor](TUNNEL_MONITOR.md) can recover failed public links and email
replacements through Outlook. Its local status page is http://127.0.0.1:13011.

The frontend, backend and Supabase run as **one Docker Compose project** named
`bom-supabase`, with a separate container for each service. PostgreSQL data stays
in its existing named volume. No personal Docker Hub login is needed.

## Everyday startup

1. Open Docker Desktop and wait until the engine is running (Linux containers).
2. Start Ollama on Windows for chat and embeddings. Its existing models are
   `qwen3.8:latest` and `qwen3-embedding:0.6b`.
3. Open PowerShell and run:

```powershell
cd C:\ProgramData\BOM-Supabase
.\Compose.ps1 start
```

Open **http://localhost:3010**. The API is at http://127.0.0.1:8011/docs and
Supabase Studio at http://127.0.0.1:18000. These addresses are for people using
this shared PC, including through Remote Desktop. They are not LAN endpoints.
The backend reaches Supabase inside Docker; the browser goes through the frontend.

If PowerShell blocks the unsigned local script, run this instead:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ProgramData\BOM-Supabase\Compose.ps1 start
```

That exception applies to this PowerShell process only. No machine policy changes.

The script calls Docker Compose with all three configuration layers. If you
prefer the equivalent Compose command in a clean PowerShell session:

```powershell
cd C:\ProgramData\BOM-Supabase\runtime\stack
docker compose --project-name bom-supabase --env-file .env -f docker-compose.yml -f compose.override.yml -f compose.app.yml up -d --no-build --wait
```

Use the script for routine operations; it also prevents shell variables from
overriding the deployment's generated settings.

## Check, stop, troubleshoot

```powershell
.\Compose.ps1 status
.\Compose.ps1 logs -Service backend
.\Compose.ps1 logs -Service frontend
.\Compose.ps1 stop
```

Press Ctrl+C to exit logs. `stop` stops all services and retains the database.
`start` recreates missing containers and uses the existing data. Do not run
`docker compose down -v`, delete the database volume, or reset Docker Desktop:
those actions can remove persisted data. Source development can run alongside
Docker: `make dev` uses frontend port 3011 and API port 8012, while Docker keeps
3010 and 8011.

If the app works but chat does not, check Ollama on Windows. The backend uses
`http://host.docker.internal:11434/v1`; `localhost` inside a container means that
container. Model names and other private runtime settings are in
`runtime\backend.docker.env`. Apply edits with `.\Compose.ps1 update-app`.
Studio credentials are in `runtime\stack\.env`; keep that file private.

## Updating the application

Normal startup needs only Docker and these installed files, not the original
OneDrive checkout or Python virtual environment. For a source update, use a
maintained repository checkout and Python 3.11+ to stage the new build context:

```powershell
# Run from the repository root, after reviewing the new source.
python infra\supabase\install_app.py --deployment-root C:\ProgramData\BOM-Supabase
cd C:\ProgramData\BOM-Supabase
.\Compose.ps1 build
.\Compose.ps1 update-app
.\Compose.ps1 status
```

The installer replaces only its generated `app-source` directory and preserves
private runtime settings. Building does not interrupt the running app. Run
`update-app` only after a successful build; it replaces the two app containers.
Take a database backup before updates that change schema or data. Supabase
version upgrades use the separate procedure in README.md.

On the Intel network, if package downloads require the corporate proxy, set
these in the PowerShell session before building (never put credentials here):

```powershell
$env:HTTP_PROXY = 'http://proxy-png.intel.com:912'
$env:HTTPS_PROXY = $env:HTTP_PROXY
.\Compose.ps1 build
```

## Passing it to the next maintainer

Hand over the repository, this deployment folder through protected team storage,
and tested database backups. Docker images contain application code, **not the
database rows**. Use the backup/restore instructions in README.md when moving to
another machine; do not just send a container. A private registry is optional.

The current database backup has been restore-tested. Daily scheduling and an
off-PC backup copy still need the team's account and storage destination.

Containers restart automatically when the Docker engine starts, unless explicitly
stopped. **Docker Desktop and the Windows Ollama installation still depend on the
current Windows account/startup arrangement.** This work does not make the PC
independent of Windows sign-in. Have IT arrange team ownership of Docker's WSL
data, Ollama/models and deployment-folder access, then verify a reboot before
disabling the departing account. No automatic Windows login is configured.

Implementation references: [Next.js standalone output](https://nextjs.org/docs/app/api-reference/config/next-config-js/output)
and [Docker Compose runtime environment files](https://docs.docker.com/compose/how-tos/environment-variables/set-environment-variables/).
