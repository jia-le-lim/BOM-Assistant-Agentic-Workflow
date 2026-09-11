# BOM-Assistant-Agentic-Workflow

On the shared host, the frontend, backend and Supabase run together in Docker.
Start Docker Desktop and Ollama, then run:

```powershell
cd C:\ProgramData\BOM-Supabase
.\Compose.ps1 start
```

Open http://localhost:3010. See [the simple Docker guide](infra/supabase/START_HERE.md)
for status, logs, stopping, updating and handover.

For source development, leave Supabase running and stop the two app containers
to free ports 3010 and 8011. Run from the repository root:

```powershell
docker stop bom-supabase-frontend-1 bom-supabase-backend-1
make dev
```

Open http://localhost:3010. The backend API runs at http://127.0.0.1:8011/docs.
Keep Ollama running for local chat and preference embeddings.

The Makefile uses `.venv-ollama` when present, otherwise `.venv`. Override this
with `make dev VENV_DIR=your-environment` if needed. Configuration and local
model setup are documented in [backend/README.md](backend/README.md).

On Windows, install GNU Make if the command is unavailable, then open a new
terminal to load the updated PATH:

```powershell
winget install --id ezwinports.make --exact --scope user
```
