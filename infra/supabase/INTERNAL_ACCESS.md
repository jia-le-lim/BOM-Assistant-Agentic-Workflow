# Share the BOM Assistant on the office network

**Pilot address: http://10.138.215.247:13010**

Individual pilot logins **`tcb-1`** and **`epoxy-1`** are active, each with a unique
password. Get their passwords from
`C:\ProgramData\BOM-Supabase\pilot-accounts.local.json` and share each login
privately with its intended tester. The original `pilot` login remains available.

The app displays the signed-in username, and the gateway supplies that username
to the backend for activity attribution. Selecting a different test role keeps
the same signed-in user. Both accounts see the same pilot data; their names do
not filter batches by department. The role selector remains available for testing.

To switch accounts, close all InPrivate/Incognito windows and open a fresh private
window, then use the other login. The browser caches HTTP Basic credentials.

The gateway runs on the Ethernet IP and requires a login for both pages and API
requests. Direct frontend port 3010, backend port 8011 and Supabase Studio remain
bound to localhost. Cloudflare is stopped and is not required for this address.
This internal address uses HTTP; it is intended for the trusted office network.

## Current status

The Ethernet address was verified from this host: anonymous access is denied;
authenticated access loads all 10 batches. Both individual accounts passed
live browser checks, including identity preservation after role changes and
rejection of the other account's password. A later test from Wi-Fi client
`172.21.222.159` reached this host by ping but failed to connect to TCP 13010.

On 2026-09-11, the Windows firewall rule was saved for all private IPv4 client
ranges: `10.0.0.0/8`, `172.16.0.0/12`, and `192.168.0.0/16`. The remote TCP
retest still failed. Further inspection showed `PrimaryStatus: Inactive` and
`EnforcementStatus: CategoryDisabled`: the saved Windows rule is not enforced.
CrowdStrike Falcon Sensor is registered as a firewall provider and owns rule
categories on this host. IT needs to check its firewall policy for this service,
as well as Wi-Fi-to-Ethernet network policy. The exact packet drop location has
not been established. See [NETWORK_ACCESS_REQUEST.md](NETWORK_ACCESS_REQUEST.md)
for the connection details and evidence to send to IT.

To reapply the rule, open **PowerShell as Administrator** on this shared PC:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\ProgramData\BOM-Supabase\Enable-LanFirewall.ps1
```

The script enables one inbound TCP rule for local address `10.138.215.247`, port
`13010`, Ethernet, and the three private client ranges above. It does not disable the
firewall or change Windows' network classification. The Ethernet is currently
classified as Public, so the address/interface/subnet restrictions apply across
profiles. The rule is named `BOM-Assistant-Pilot-LAN-13010`.

A colleague should open the pilot address and repeat
`Test-NetConnection 10.138.215.247 -Port 13010` if it still fails. Corporate
network routing and access policies must also permit connections between the
client and this host. Internal clients using addresses outside these private
ranges require an additional approved source range.

## Starting and stopping

After Docker Desktop and Ollama are running:

```powershell
cd C:\ProgramData\BOM-Supabase
.\Compose.ps1 start
.\Pilot.ps1 local-start
```

`local-start` starts the internal password gateway without starting Cloudflare.
The LAN gateway restarts with Docker unless explicitly stopped. To close pilot
access while leaving the application/database running:

```powershell
.\Pilot.ps1 stop
```

## Address changes and handover

To add more accounts from a maintained checkout:

```powershell
python infra\supabase\pilot_users.py --add tcb-2 epoxy-2
```

Existing account passwords are preserved. Apply the rendered configuration by
restarting `bom-pilot-pilot-gateway-1` in Docker Desktop, or with
`docker restart bom-pilot-pilot-gateway-1`. Login details stay in the protected
`pilot-accounts.local.json`; do not share that whole file with testers.

This Ethernet address currently comes from DHCP. Ask IT for a DHCP reservation
for reliable handover. If the address changes, update the configuration from a
maintained repository checkout using Python 3.11+:

```powershell
python infra\supabase\configure_lan.py --address NEW_INTERNAL_IP --clients 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16
```

Then run `Pilot.ps1 local-start` and have an administrator rerun the firewall
script. `runtime\lan.json` records the address and allowed client ranges. Do not
replace this with an unrestricted `0.0.0.0` bind or an Internet-facing firewall
rule. Existing Windows account/startup handover requirements still apply.

The role selector remains the application's pilot access model: trusted testers
can select privileged roles and modify or export the live migrated BOM data.
