"""Verify pilot workspace isolation through real sessions without changing BOM data."""
import argparse
import http.cookiejar
import json
from pathlib import Path
import urllib.error
import urllib.request


def verify(root: Path, base: str):
    accounts = json.loads((root / "pilot-accounts.local.json").read_text(encoding="utf-8"))
    sessions = {}
    visible = {}

    def request(opener, path, *, body=None, spoof=None):
        headers = {"Origin": base, "Content-Type": "application/json"}
        if spoof:
            headers.update({"X-User": spoof, "X-Role": "admin"})
        req = urllib.request.Request(base + path, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with opener.open(req, timeout=30) as response:
                data = response.read()
                return response.status, json.loads(data) if data else None, response.headers
        except urllib.error.HTTPError as exc:
            return exc.code, None, exc.headers

    try:
        for user in ("tcb-1", "epoxy-1"):
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            assert request(opener, "/api/backend/batches")[0] == 401
            status, _, _ = request(opener, "/api/auth/login", body={
                "identifier": user, "password": accounts[user]["password"]})
            assert status == 200, f"Login failed for {user}"
            sessions[user] = opener
            other = "epoxy-1" if user == "tcb-1" else "tcb-1"
            status, identity, _ = request(opener, "/api/pilot-session", spoof=other)
            assert status == 200 and identity["user"] == user, "Browser identity headers were trusted"
            status, batches, headers = request(opener, "/api/backend/batches", spoof=other)
            assert status == 200 and isinstance(batches, list)
            assert "no-store" in headers.get("Cache-Control", "")
            assert all(batch["uploaded_by"] == user for batch in batches), "Foreign workspace in list"
            visible[user] = {batch["batch_id"] for batch in batches}
            for bid in visible[user]:
                assert request(opener, f"/api/backend/batches/{bid}/summary")[0] == 200
        assert visible["tcb-1"].isdisjoint(visible["epoxy-1"])
        denied = 0
        for user, opener in sessions.items():
            other = "epoxy-1" if user == "tcb-1" else "tcb-1"
            for bid in visible[other]:
                for path in (f"batches/{bid}/summary", f"recommendations?batch_id={bid}",
                             f"export/wings?batch_id={bid}", f"export/wings.xlsx?batch_id={bid}",
                             f"pending-changes?batch_id={bid}", f"assist/{bid}", f"similarity/{bid}",
                             f"config/dormant-rules/coverage?batch_id={bid}",
                             f"config/part-categories/coverage?batch_id={bid}"):
                    assert request(opener, "/api/backend/" + path, spoof=other)[0] == 404, path
                    denied += 1
        print(json.dumps({"verified": True, "workspace_counts": {u: len(ids) for u, ids in visible.items()},
                          "foreign_requests_denied": denied, "spoofed_identity_ignored": True}))
    finally:
        for opener in sessions.values():
            request(opener, "/api/auth/logout", body={})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment-root", type=Path, default=Path("C:/ProgramData/BOM-Supabase"))
    parser.add_argument("--base-url", default="http://127.0.0.1:13010")
    args = parser.parse_args()
    verify(args.deployment_root, args.base_url.rstrip("/"))
