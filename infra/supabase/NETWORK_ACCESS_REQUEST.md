# Internal BOM Assistant connection request

Please allow internal pilot users to reach the BOM Assistant login gateway on
the shared Windows host, and check the CrowdStrike firewall policy plus routing
and access rules between office Wi-Fi/VPN and this Ethernet host.

| Setting | Value |
| --- | --- |
| Destination | `10.138.215.247`, TCP `13010`, inbound on Ethernet |
| Requested clients | All internal client networks; known private ranges `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` |
| Failing test client | `172.21.222.159`, Wi-Fi |
| Service URL | `http://10.138.215.247:13010` |
| Listening process | `C:\Program Files\Docker\Docker\resources\com.docker.backend.exe` |
| Container | `bom-pilot-pilot-gateway-1`, host port 13010 to container port 8080 |

Evidence collected on 2026-09-11:

- The remote client can ping the host (4 ms), but `Test-NetConnection` reports
  `TcpTestSucceeded: False`, including after the local Windows allow rule was saved.
- Docker Compose is running. All 13 application/database containers are healthy.
- The gateway listens on `10.138.215.247:13010` and `127.0.0.1:13010`.
- Local requests to the Ethernet address reach the login gate and return HTTP 401
  without credentials. Authenticated gateway checks load all 10 batches.
- Windows rule `BOM-Assistant-Pilot-LAN-13010` exists and is enabled for the requested
  private ranges, but ActiveStore reports `PrimaryStatus: Inactive` and
  `EnforcementStatus: CategoryDisabled`.
- Windows Security Center lists CrowdStrike Falcon Sensor as a firewall provider.
  `HNetCfg.FwProducts` confirms its registered rule categories are `2` and `0`.
  Microsoft documents this property as indicating categories taken over by the
  third-party firewall: [RuleCategories documentation](https://learn.microsoft.com/en-us/windows/win32/api/netfw/nf-netfw-inetfwproduct-get_rulecategories).
- This establishes that the local Windows allow rule is not enforced. It does
  not establish whether CrowdStrike or an upstream network device drops this
  particular connection; please check policy/logs for the source and destination.

The requested service requires a pilot login. Database and direct application
ports remain unexposed to the network. No firewall protection needs to be disabled.

After applying the relevant policy, test from the Wi-Fi client:

```powershell
Test-NetConnection 10.138.215.247 -Port 13010
```

Expected: `TcpTestSucceeded: True`, then a login prompt when opening the URL.
The host currently uses DHCP; a reservation would keep this address stable.
