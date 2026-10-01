# Configuration and access

## Private configuration map

Paths below are relative to C:\ProgramData\BOM-Supabase unless stated otherwise. Equivalent copies are under PRIVATE/deployment in the package. Open these locally only. Documentation deliberately records locations and meanings without printing credentials.

| File | What it controls |
|---|---|
| runtime/stack/.env | PostgreSQL password, Supabase signing/API keys, Studio credentials and platform settings |
| runtime/backend.docker.env | LLM, embedding, proxy and application feature settings |
| runtime/backend.local.env | Generated host-side connection settings for local Supabase |
| runtime/pilot/auth.json | Hash-only account list mounted read-only in frontend |
| pilot-accounts.local.json | Private usernames/passwords/hashes and optional aliases |
| pilot-login.local.env | Older pilot login bootstrap; preserve as historical private configuration |
| runtime/pilot/compose.yml | Pilot services and proxy/tunnel configuration |
| runtime/pilot/compose.lan.yml | Host IP and LAN port binding |
| runtime/pilot/proxy.env | Corporate tunnel proxy settings |
| runtime/pilot/Caddyfile | Gateway routes and trusted frontend |
| runtime/lan.json | Recorded LAN address and client subnets |
| runtime/pilot/monitor/email.local.json | Monitor sender, recipients and delivery adapter |
| runtime/pilot/monitor/control.json | Whether automatic recovery is enabled |
| runtime/pilot/monitor/status.json | Last observed monitor state; historical after copying |
| backend-cloud-before-cutover.env, source.local.env | Historical cloud connection and migration credentials |
| Repository backend/.env | Development database/model settings; copied separately to PRIVATE/workspace |
| Repository frontend/.env.local and frontend/.local | Development authentication settings and hash-only account file, if present |

Do not print a full docker inspect, environment dump, Compose config or error log into a ticket. The package keeps full container inspection under PRIVATE because Docker environment values can contain secrets. Use config --quiet when only validating syntax.

## Application settings

| Variable | Role and operational notes |
|---|---|
| SUPABASE_URL | REST gateway URL; containers use api-gw, host uses loopback |
| SUPABASE_SECRET_KEY | Server-only API credential; never NEXT_PUBLIC or browser code |
| SUPABASE_SERVICE_ROLE_KEY | Legacy server key; production Compose explicitly clears it |
| DATABASE_URL | Direct database selector; nonempty overrides REST; production Compose clears it |
| BOM_ALLOW_SQLITE | Test-only; production explicitly sets 0 |
| BOM_ENGINE | Unset/default uses statistical engine; rules selects legacy calculation |
| BOM_WORKSPACE_READ_ALL_USERS | Explicit usernames allowed shared reads; captured value was pilot |
| LLM_BASE_URL | Docker uses http://host.docker.internal:11434/v1 |
| LLM_MODEL | Captured configured tag qwen3.8:latest |
| LLM_API_KEY | Provider credential or local placeholder; confidential file only |
| LLM_TIMEOUT_S | Captured value 180; includes loading/generation delays |
| LLM_REDACT_PROMPTS | Masks configured sensitive fields; review policy before changing model endpoints |
| MEM0_ENABLED | Captured value 1; enabling does not guarantee readiness |
| MEM0_VECTOR_STORE | Captured supabase_rest path does not require optional mem0 OSS packages |
| MEM0_COLLECTION_NAME | bom_engineer_memory_qwen3_1024 |
| MEM0_EMBEDDING_MODEL | qwen3-embedding:0.6b; verify server actually exposes it |
| MEM0_EMBEDDING_DIMS | 1024; must match vector schema and model |
| MEM0_DATABASE_URL | Optional direct-memory path; production Compose clears it |
| HTTP_PROXY, HTTPS_PROXY, NO_PROXY | Corporate routing; exclude local/internal service names from proxy |
| BACKEND_URL | Frontend server target; Docker http://backend:8011, dev 127.0.0.1:8012 |
| PILOT_AUTH_REQUIRED, PILOT_AUTH_FILE | Enable login and identify hash-only account file |

Backend environment variables already present in the process override backend/.env. The installed Compose wrappers clear conflicting generated variables before running Docker. Editing repository backend/.env does not change runtime/backend.docker.env or a running container.

Do not copy backend/.env.example unchanged and assume local database access works: it still includes a historical cloud/direct URL placeholder. For local development, deliberately merge the generated local connection settings and clear DATABASE_URL and legacy keys.

## Authentication and authorization

The login implementation is custom pilot authentication, not Supabase Auth or Entra ID. Pilot accounts use bcrypt hashes; server-side sessions are opaque and held in the frontend process, with an eight-hour lifetime. Restarting that process signs everyone out. Account removal or hash changes invalidate matching sessions.

Authenticated pilot sessions receive the admin capability role in current code. The frontend ignores browser-supplied user/role identity and replaces it with the validated session. Admin capability does not bypass workspace ownership. Headers X-User and X-Role are not standalone authentication and must never be trusted from a public backend endpoint.

Workspace ownership follows batches.uploaded_by. Explicitly configured shared readers may read allowed workspace views; they cannot edit someone else's records or read private chats/settings/reminders. Account-owned settings and reminders stay private. Renaming or deleting a login does not migrate database ownership.

A cross-account approval problem remains: decisions that require a different senior approver still obey the owner restriction and self-approval prohibition. Shared-read access does not solve this. Preserve these boundaries until the team designs, tests and approves an explicit reviewer-sharing workflow.

## Adding or rotating a pilot account

Back up the private account files first. From a maintained checkout, the account tool adds named accounts without printing passwords:

~~~powershell
& 'C:\Program Files\Python313\python.exe' infra\supabase\pilot_users.py --deployment-root 'C:\ProgramData\BOM-Supabase' --add next-engineer
~~~

The tool preserves existing accounts, creates missing passwords and hashes, validates the gateway, and writes the hash-only frontend file. Existing saved hashes are reused. Therefore, editing only a plaintext password field is not a rotation: regenerate/remove that account's stored hash before rendering, following a reviewed private-file edit. Do not pass passwords on a command line.

After rendering, recreate frontend and gateway so their bind mounts point at the intended file versions, then test login, logout and ownership from a browser. Removing a user requires an explicit edit of the private account list and rerender; the tool has no deletion flag. Preserve historical user identifiers in audit/business records. Agree disposition of their workspaces before disabling access.

## Credential rotation

Rotate one coherent credential set at a time and retain a pre-change backup. Pilot login hashes, Studio login, PostgreSQL role passwords and Supabase JWT/API keys are different credentials with different consumers. Changing a value only in .env does not necessarily update the live PostgreSQL role or every token consumer.

For platform signing/API/database-key rotation, use the pinned release's documented coordinated process, rebuild/recreate affected services, and verify SQL, Auth, REST, server RPC and anonymous denial. Never make the database public as a workaround for a key mismatch. No platform credentials were rotated during packaging.

## Network and proxy care

The corporate proxy in existing guidance is proxy-png.intel.com:912. Recheck routing on the target host. Preserve local exclusions such as localhost, 127.0.0.1, ::1, backend, frontend, api-gw, rest, db and host.docker.internal. Container localhost means the container, not Windows.

The optional public tunnel wraps traffic through the corporate HTTP proxy. Its temporary URL can change after restart. The monitor can restart failed tunnels and submit replacement-link email through classic Outlook; it cannot start Docker or sign into Outlook.

For team ownership, update the task principal, Python path, recipient/sender settings and Outlook mailbox. A successful Outlook submission is not proof of inbox delivery. This handover does not send notices or re-register tasks.

Store the package and active secrets in approved restricted team storage. The package has a restrictive local ACL, but it is not encrypted and remains in the current OneDrive workspace until moved. Check inherited cloud sharing separately; a local ACL does not change OneDrive permissions. Rotate handed-over secrets at the agreed ownership transition.

