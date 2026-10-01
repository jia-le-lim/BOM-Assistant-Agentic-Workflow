"""Verify all handover files against SHA256SUMS.json without modifying them."""
import hashlib
import json
from pathlib import Path
import sys

def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def verify(root):
    root = root.resolve()
    records = json.loads((root / "SHA256SUMS.json").read_text(encoding="utf-8"))["files"]
    expected, failures, total = set(), [], 0
    for index, item in enumerate(records, 1):
        name = item["path"]
        path = (root / name).resolve()
        if not path.is_relative_to(root) or path == root or name in expected:
            raise ValueError("Unsafe or duplicate manifest path")
        expected.add(name)
        if not path.is_file():
            failures.append(f"Missing: {name}")
        elif path.stat().st_size != item["bytes"] or digest(path) != item["sha256"]:
            failures.append(f"Changed: {name}")
        total += item["bytes"]
        if index % 200 == 0:
            print(f"Checked {index}/{len(records)} files", flush=True)
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    failures.extend("Unexpected: " + n for n in sorted(actual - expected - {"SHA256SUMS.json"}))
    for failure in failures:
        print(failure)
    if failures:
        raise SystemExit(1)
    print(f"VERIFIED {len(records)} files; {total:,} bytes. No missing, changed or unexpected files.")
    return len(records), total

if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python verify_package.py PACKAGE_FOLDER")
    verify(Path(sys.argv[1]))

