"""Live E2E smoke test against a running uvicorn instance (port 8011).

Drives the full PRD workflow over HTTP with the real Jan'26 file:
upload -> score -> triage queue -> review -> senior approval -> export -> chat.
"""

import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8011"
CSV = Path(__file__).resolve().parents[2] / "BOM table" / "BOM REVIEW_Jan'26 .csv"
ENG = {"X-User": "alice", "X-Role": "engineer"}
SENIOR = {"X-User": "boss", "X-Role": "senior"}


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=120)

    for attempt in range(20):
        try:
            r = c.get("/health")
            break
        except httpx.ConnectError:
            time.sleep(0.5)
    else:
        print("FAIL: server never came up"); return 1
    print(f"health: {r.json()}")

    t0 = time.time()
    with CSV.open("rb") as f:
        r = c.post("/upload-bom-file", files={"file": (CSV.name, f, "text/csv")},
                   data={"label": "Jan26-live", "module_filter": "TCB"}, headers=ENG)
    r.raise_for_status()
    up = r.json()
    print(f"upload [{time.time()-t0:.1f}s]: batch={up['batch_id']} "
          f"loaded={up['rows_loaded']} quarantined={up['rows_quarantined']}")
    b = up["batch_id"]

    t0 = time.time()
    r = c.post(f"/run-recommendation?batch_id={b}", headers=ENG)
    r.raise_for_status()
    run = r.json()
    print(f"score  [{time.time()-t0:.1f}s]: scored={run['rows_scored']} "
          f"reviewY={run['review_required_Y']} rule={run['rule_version']}")

    # The triage queue an engineer would actually work: highest exposure first
    q = c.get(f"/recommendations?batch_id={b}&status=pending_review&limit=3",
              headers=ENG).json()
    print(f"queue: total_pending={q['total']}")
    top = q["items"][0]
    print(f"  top item {top['item_id']}: ${top['exposure_usd']:,.0f} "
          f"{top['risk_level']} [{top['reason_code'][:60]}...]")

    # Review the top two: one accept (may need senior), one reject
    i1, i2 = q["items"][0], q["items"][1]
    r = c.post(f"/review/{i1['item_id']}?batch_id={b}",
               json={"decision": "accept", "comment": "live smoke accept"},
               headers=ENG).json()
    print(f"review {i1['item_id']}: accept -> {r['status']}")
    if r["requires_senior_approval"]:
        a = c.post(f"/review/{i1['item_id']}/approve?batch_id={b}", headers=SENIOR)
        print(f"  senior approve: {a.json()['status']}")

    r = c.post(f"/review/{i2['item_id']}?batch_id={b}",
               json={"decision": "reject", "comment": "keep current"},
               headers=ENG).json()
    print(f"review {i2['item_id']}: reject -> {r['status']}")

    e = c.get(f"/export/wings?batch_id={b}", headers=ENG)
    print(f"export: rows={e.headers['X-Rows-Exported']} "
          f"pending={e.headers['X-Pending-Review']} "
          f"awaiting_senior={e.headers['X-Awaiting-Senior']}")
    print("export preview:", e.text.splitlines()[0])
    for line in e.text.splitlines()[1:3]:
        print("               ", line)

    chat = c.post("/chat", json={"question": f"why item {i1['item_id']}?"},
                  headers=ENG).json()
    print(f"chat: {chat['answer'][:140]}")

    unknown = c.post("/chat", json={"question": "predict next month demand"},
                     headers=ENG).json()
    print(f"chat guard: {unknown['answer'][:80]}")
    print("\nSMOKE TEST COMPLETE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
