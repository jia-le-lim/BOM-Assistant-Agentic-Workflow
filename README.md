# BOM-Assistant-Agentic-Workflow

On the shared host, the frontend, backend and Supabase run together in Docker.
Start Docker Desktop and Ollama, then run:

```powershell
cd C:\ProgramData\BOM-Supabase
.\Compose.ps1 start
```

Open http://localhost:3010. See [the simple Docker guide](infra/supabase/START_HERE.md)
for status, logs, stopping, updating and handover.

For source development, run from the repository root. Docker can keep running;
development uses separate ports:

```powershell
make dev
```

Open http://localhost:3011. The development API runs at http://127.0.0.1:8012/docs.
Keep Ollama running for local chat and preference embeddings.

To enable the custom login locally with the existing pilot accounts, run
`npm --prefix frontend run setup:auth` once, then open
http://localhost:3011/login. This configures local authentication without changing Docker.

Override the development ports with
`make dev FRONTEND_PORT=3020 BACKEND_PORT=8020` if needed. The frontend's
`BACKEND_URL` automatically follows `BACKEND_PORT`. Docker keeps ports 3010/8011.

The Makefile uses `.venv-ollama` when present, otherwise `.venv`. Override this
with `make dev VENV_DIR=your-environment` if needed. Configuration and local
model setup are documented in [backend/README.md](backend/README.md).

On Windows, install GNU Make if the command is unavailable, then open a new
terminal to load the updated PATH:

```powershell
winget install --id ezwinports.make --exact --scope user
```
