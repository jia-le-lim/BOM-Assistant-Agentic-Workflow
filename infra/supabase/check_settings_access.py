"""Verify live pilot settings ownership without changing saved rule values."""

import argparse
import http.cookiejar
import json
from pathlib import Path
import urllib.error
import urllib.request


def verify(root: Path, base: str):
    accounts = json.loads((root / "pilot-accounts.local.json").read_text(encoding="utf-8"))
    users = ("tcb-1", "epoxy-1")
    sessions, configs, rules = {}, {}, {}

    def request(opener, path, *, body=None, method=None, spoof=None):
        headers = {"Origin": base, "Content-Type": "application/json"}
        if spoof:
            headers.update({"X-User": spoof, "X-Role": "admin"})
        req = urllib.request.Request(base + path, headers=headers, method=method,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with opener.open(req, timeout=30) as response:
                data = response.read()
                if "application/json" in response.headers.get("Content-Type", ""):
                    data = json.loads(data) if data else None
                return response.status, data
        except urllib.error.HTTPError as exc:
            return exc.code, None

    try:
        for user in users:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            assert request(opener, "/api/backend/config/rules")[0] == 401
            status, _ = request(opener, "/api/auth/login", body={
                "identifier": user, "password": accounts[user]["password"]})
            assert status == 200, f"Login failed for {user}"
            sessions[user] = opener
            other = users[1] if user == users[0] else users[0]
            status, identity = request(opener, "/api/pilot-session", spoof=other)
            assert status == 200 and identity["user"] == user
            status, configs[user] = request(opener, "/api/backend/config/rules")
            assert status == 200, "Private settings could not load"
            status, spoofed = request(opener,
                f"/api/backend/config/rules?owner_user={other}", spoof=other)
            assert status == 200 and spoofed == configs[user], "Owner override changed settings"
            status, dormant = request(opener, "/api/backend/config/dormant-rules", spoof=other)
            assert status == 200, "Private dormant rules could not load"
            rules[user] = {row["rule_id"] for row in dormant["rules"]}
            assert request(opener, "/api/backend/config/part-categories")[0] == 200
            for path in ("/config", "/config/dormant"):
                status, html = request(opener, path)
                assert status == 200 and b"Private to your account" in html, "Updated Settings UI missing"

        assert rules[users[0]].isdisjoint(rules[users[1]]), "Accounts share dormant rule records"
        denied = 0
        for user, opener in sessions.items():
            other = users[1] if user == users[0] else users[0]
            for rule_id in rules[other]:
                path = f"/api/backend/config/dormant-rules/{rule_id}"
                status, _ = request(opener, path + "/confirm", method="POST", spoof=other)
                assert status == 404, f"Foreign confirmation returned {status}, expected 404"
                status, _ = request(opener, path, method="DELETE", spoof=other)
                assert status == 404, f"Foreign deletion returned {status}, expected 404"
                denied += 2
            assert request(opener, "/api/backend/config/rules")[1] == configs[user]
        report = {"verified": True, "accounts_checked": len(users),
                  "dormant_rules_per_account": {u: len(ids) for u, ids in rules.items()},
                  "distinct_dormant_rule_records": True, "foreign_mutations_denied": denied,
                  "spoofed_identity_ignored": True, "private_settings_ui_present": True,
                  "saved_rule_values_unchanged": True}
        print(json.dumps(report))
        return report
    finally:
        for opener in sessions.values():
            request(opener, "/api/auth/logout", body={})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment-root", type=Path, default=Path("C:/ProgramData/BOM-Supabase"))
    parser.add_argument("--base-url", default="http://127.0.0.1:13010")
    args = parser.parse_args()
    verify(args.deployment_root, args.base_url.rstrip("/"))
