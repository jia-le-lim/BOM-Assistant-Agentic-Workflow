"""Verify an observer's shared workspace reads through real sessions, without BOM writes."""

import argparse
import http.cookiejar
import json
from pathlib import Path
import urllib.error
import urllib.request


def verify(root: Path, base: str, observer: str):
    accounts = json.loads((root / "pilot-accounts.local.json").read_text(encoding="utf-8"))
    if observer not in accounts:
        raise ValueError("Observer must be an existing pilot login")
    sessions, visible = {}, {}

    def request(opener, path, body=None):
        # The gateway must ignore browser-supplied identity even for the observer.
        req = urllib.request.Request(base + path,
            headers={"Origin": base, "Content-Type": "application/json", "X-User": observer, "X-Role": "admin"},
            data=json.dumps(body).encode() if body is not None else None)
        try:
            with opener.open(req, timeout=30) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            return exc.code, None

    try:
        for user in dict.fromkeys([observer, "tcb-1", "epoxy-1"]):
            if user not in accounts:
                continue
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            assert request(opener, "/api/backend/batches")[0] == 401
            status, _ = request(opener, "/api/auth/login", {
                "identifier": user, "password": accounts[user]["password"]})
            assert status == 200, f"Login failed for {user}"
            sessions[user] = opener
            status, identity = request(opener, "/api/pilot-session")
            assert status == 200 and identity["user"] == user
            status, rows = request(opener, "/api/backend/batches")
            assert status == 200 and isinstance(rows, list)
            visible[user] = rows
            if user != observer:
                assert all(row["uploaded_by"] == user for row in rows)

        observer_ids = {row["batch_id"] for row in visible[observer]}
        for user, rows in visible.items():
            assert {row["batch_id"] for row in rows} <= observer_ids

        checked = 0
        for owner in {row["uploaded_by"] for row in visible[observer]} - {observer}:
            workspace = next(row for row in visible[observer] if row["uploaded_by"] == owner)
            bid = workspace["batch_id"]
            status, summary = request(sessions[observer], f"/api/backend/batches/{bid}/summary")
            assert status == 200 and summary["batch"]["uploaded_by"] == owner
            assert summary["read_only"] is True
            status, queue = request(sessions[observer], f"/api/backend/recommendations?batch_id={bid}&limit=1")
            assert status == 200 and isinstance(queue["items"], list)
            for user, opener in sessions.items():
                if user not in {observer, owner}:
                    assert request(opener, f"/api/backend/batches/{bid}/summary")[0] == 404
            checked += 1
        assert checked > 0, "No foreign workspace was available to verify"
        report = {"verified": True, "observer": observer,
                  "workspace_counts": {user: len(rows) for user, rows in visible.items()},
                  "foreign_owners_checked": checked, "spoofed_identity_ignored": True}
        print(json.dumps(report))
        return report
    finally:
        for opener in sessions.values():
            request(opener, "/api/auth/logout", {})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment-root", type=Path, default=Path("C:/ProgramData/BOM-Supabase"))
    parser.add_argument("--base-url", default="http://127.0.0.1:13010")
    parser.add_argument("--user", required=True)
    args = parser.parse_args()
    verify(args.deployment_root, args.base_url.rstrip("/"), args.user)
