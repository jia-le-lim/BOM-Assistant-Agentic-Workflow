# Ownership and acceptance

The technical package preserves the project, but the team still needs named owners and access under their own accounts. Complete this page together before disabling the departing account. Blank owner fields are intentional; no successor, storage destination or policy was provided.

## Ownership register

| Responsibility | Primary owner | Backup owner | Access or record to provide |
|---|---|---|---|
| Business workflow and WINGS approval policy | To assign | To assign | Required decision/approval process |
| Application code and releases | To assign | To assign | Team repository, branch/review access |
| PostgreSQL and restore rehearsals | To assign | To assign | Deployment, Docker, backup and secrets access |
| Windows host and Docker/WSL | To assign | To assign | Team account, licensing, reboot/startup support |
| Ollama models and GPU capacity | To assign | To assign | Model store, startup account, drivers |
| Network, firewall and proxy | To assign | To assign | IT owner, stable host address, client CIDRs |
| Pilot logins and ownership changes | To assign | To assign | Private account file and access policy |
| Backup monitoring and retention | To assign | To assign | Team share, job credentials, alerts |
| Optional tunnel and email monitor | To assign | To assign | Task account, sending mailbox, recipients |

Record host name from inventory/host.json, new app URL, operating hours, approved secret-store location, backup share, retention, recovery point target, recovery time target, support contact and next review date in the team's operational record.

## Current issues and decisions

| Priority | Observation | Next action and completion evidence |
|---|---|---|
| Before departure | No BOM-Supabase-Backup task registered | Assign account/share, install task, run it and verify off-host archive plus manifest |
| Before departure | Docker Desktop/WSL and Ollama depend on account/startup arrangement | IT establishes supported team operation; successor demonstrates a cold reboot with old account signed out |
| Before departure if monitor retained | BOM-Cloudflare-Monitor runs as jialelim | Transfer task/mailbox/recipient ownership and test under new account |
| Before moving | Package is in current user's OneDrive workspace | Transfer to approved restricted team storage, verify hashes and audit cloud sharing |
| Before feature rollout | Current source differs from installed app; reminder table absent live | Reproduce installed baseline, then review and deploy new source with backup |
| Before claiming preference recall ready | Configured embedding tag is missing from running Ollama API list | Identify actual server/store, expose matching copied embedding model, verify readiness and dimensions |
| Before formal approval workflow adoption | Separate-approver requirement conflicts with owner-only review writes | Product/application owners decide explicit cross-account approval design; tests must preserve other privacy boundaries |
| Before relying on LAN URL | Captured IP is host-specific; prior documents conflict on remote reachability | Reserve/update address and verify from intended client networks |
| Before full-platform disaster recovery claim | Only application restore was rehearsed | Rehearse exact release on separate engine, including any Auth/Storage/internal data now in use |
| Planned maintenance | Startup DDL and privileged runtime role are coupled | Consider formal migrations and tighter runtime privileges as a separate engineered change |
| Planned maintenance | Historical docs have old counts, Python/Node requirements and access descriptions | Prefer this dated runbook, current source and captured inventories; update after releases |

These findings were documented without changing production to resolve them. The live stack stayed running throughout capture and application restore rehearsal.

## Successor acceptance exercise

- [ ] The successor can access the team source repository and the full private package.
- [ ] Package hashes verify after transfer; database .dump.json is retained with the archive.
- [ ] The successor can find credentials without asking the departing engineer.
- [ ] The successor can start, inspect and stop main services using installed scripts.
- [ ] The successor can log in with the correct identity and view the expected workspaces.
- [ ] A second PC on each supported network can reach the gateway; anonymous API access is denied.
- [ ] Shared reads and private settings/chats behave according to intended account rules.
- [ ] A separate-host database restore produces matching table counts and representative records.
- [ ] A rehearsal workspace can upload, score, review and export without touching live WINGS data.
- [ ] Chat retrieval works; quantities remain under explicit human control.
- [ ] Both configured model names are available; preference recall is verified.
- [ ] Newer features such as reminders are tested only after their reviewed deployment.
- [ ] Backup task succeeds while the old user is signed out and files reach another machine.
- [ ] The team agrees retention, recovery point and recovery time targets.
- [ ] A cold reboot starts the required stack under the new supported ownership arrangement.
- [ ] Monitor recovery and any approved notification delivery work under the intended account.
- [ ] The team records the deployed source/image versions and acceptance date.

Accepted by: ____________________  
Date and time: ____________________  
Unresolved items and responsible owner: ____________________  
Next recovery rehearsal: ____________________

Do not mark these boxes merely because the packaging tests passed. Each describes an operational capability the receiving team must demonstrate.

