"""Assemble source, browser documentation and checksums after confidential capture."""
from datetime import datetime, timezone
import argparse
import hashlib
import html
import json
from pathlib import Path
import posixpath
import re
import shutil
import subprocess
import tarfile

REPO = Path(__file__).resolve().parents[2]

def run(args):
    return subprocess.run([str(x) for x in args], cwd=REPO, capture_output=True, check=True).stdout

def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def render_inline(value, doc_names):
    value = html.escape(value)
    value = re.sub(r"\x60([^\x60]+)\x60", r"<code>\1</code>", value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", value)
    def link(match):
        label, url = match.groups()
        if url in doc_names:
            url = "#" + Path(url).stem
        elif not url.startswith(("http://", "https://", "#")):
            url = posixpath.normpath("source/docs/handover/" + url)
        return '<a href="' + url + '">' + label + "</a>"
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, value)

def render_markdown(text, names):
    lines = text.splitlines()
    output, paragraph = [], []
    listing, code, index = None, None, 0
    def flush():
        if paragraph:
            output.append("<p>" + render_inline(" ".join(paragraph), names) + "</p>")
            paragraph.clear()
    def close_list():
        nonlocal listing
        if listing:
            output.append("</" + listing + ">")
            listing = None
    while index < len(lines):
        line = lines[index]
        index += 1
        if line.startswith(("~~~", chr(96)*3)):
            flush()
            close_list()
            if code is None:
                code = []
            else:
                output.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
                code = None
            continue
        if code is not None:
            code.append(line)
            continue
        if line.startswith("|"):
            flush()
            close_list()
            rows = [line]
            while index < len(lines) and lines[index].startswith("|"):
                rows.append(lines[index])
                index += 1
            output.append("<div class='table-wrap'><table>")
            for n, row in enumerate(rows):
                cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
                if all(re.fullmatch(r"[-: ]+", c) for c in cells):
                    continue
                tag = "th" if n == 0 else "td"
                output.append("<tr>" + "".join("<"+tag+">"+render_inline(c,names)+"</"+tag+">" for c in cells) + "</tr>")
            output.append("</table></div>")
            continue
        heading = re.match(r"^(#{1,6}) (.+)", line)
        bullet = re.match(r"^(?:- |(\d+)\. )(.+)", line)
        if heading:
            flush()
            close_list()
            level = min(len(heading[1]) + 1, 6)
            output.append(f"<h{level}>" + render_inline(heading[2], names) + f"</h{level}>")
        elif bullet:
            flush()
            desired = "ol" if bullet[1] else "ul"
            if listing != desired:
                close_list()
                listing = desired
                output.append("<"+listing+">")
            output.append("<li>" + render_inline(bullet[2], names) + "</li>")
        elif not line.strip():
            flush()
            close_list()
        else:
            close_list()
            paragraph.append(line)
    flush()
    close_list()
    if code is not None:
        raise ValueError("Unclosed code fence")
    return "\n".join(output)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True, type=Path)
    args = parser.parse_args()
    root = args.package.resolve()
    if not (root / "CONFIDENTIAL.txt").is_file():
        raise RuntimeError("Not a recognized captured package")
    if not json.loads((root / "inventory/capture-status.json").read_text())["complete"]:
        raise RuntimeError("Capture is incomplete")
    restore = json.loads((root / "inventory/restore-verification.json").read_text())
    if not restore["passed"]:
        raise RuntimeError("Application restore has not passed")
    source = root / "source"
    source.mkdir(exist_ok=True)
    names = run(["git","ls-files","--cached","--others","--exclude-standard","-z"]).decode("utf-8").split("\0")
    copied = []
    for name in sorted(set(names)):
        if not name or name == "docs.zip":
            continue
        original = REPO / name
        if original.is_file() and not original.resolve().is_relative_to(root):
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, target)
            copied.append(name)
    bundle = root / "PRIVATE/repository.bundle"
    if not bundle.exists():
        run(["git","bundle","create",bundle,"--all"])
    run(["git","bundle","verify",bundle])
    (root / "PRIVATE/working-tree.patch").write_bytes(run(["git","diff","--binary","HEAD"]))
    write_json(root / "inventory/source.json", {
        "head":run(["git","rev-parse","HEAD"]).decode().strip(),
        "branch":run(["git","branch","--show-current"]).decode().strip(),
        "status":run(["git","status","--short"]).decode(),
        "snapshot_files":copied,
        "note":"Source is the working snapshot; Git bundle contains committed history only."
    })
    docs = [source / "docs/handover/README.md"] + sorted((source / "docs/handover").glob("[0-9]*.md"))
    doc_names = {p.name for p in docs}
    sections, nav = [], []
    for doc in docs:
        body = doc.read_text(encoding="utf-8-sig")
        title = body.splitlines()[0].removeprefix("# ")
        nav.append('<a href="#'+doc.stem+'">'+html.escape(title)+'</a>')
        sections.append('<section id="'+doc.stem+'">'+render_markdown(body,doc_names)+'</section>')
    css = """
:root{color-scheme:light;--ink:#163148;--line:#dbe4ea;--accent:#006c82}
*{box-sizing:border-box}body{margin:0;font:16px/1.65 system-ui,Segoe UI,sans-serif;color:var(--ink);background:#f3f6f8}
header{padding:36px 5vw;background:#102f42;color:white}header p{color:#c3d8e3;margin:4px 0}header h1{margin:0;font-size:32px}
.layout{display:grid;grid-template-columns:280px minmax(0,1000px);max-width:1380px;margin:auto;gap:32px;padding:30px 24px}
nav{position:sticky;top:20px;height:fit-content;background:white;padding:20px;border:1px solid var(--line);border-radius:8px}
nav a{display:block;font-size:14px;padding:8px 0;border-bottom:1px solid #edf2f5}a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
main{min-width:0}section{background:white;padding:32px 38px;margin-bottom:26px;border:1px solid var(--line);border-radius:8px;scroll-margin-top:20px}
h2{font-size:28px;line-height:1.2;margin:0 0 24px}h3{font-size:21px;line-height:1.35;margin:32px 0 14px;color:#006479}
h4{font-size:18px}p{margin:12px 0}li{margin:6px 0}code{font:13px/1.5 Consolas,monospace;overflow-wrap:anywhere}
p code,td code{background:#edf3f6;padding:2px 4px;border-radius:3px}pre{background:#102f42;color:#edf7fa;padding:18px;overflow:auto;border-radius:6px}
pre code{white-space:pre;overflow-wrap:normal}.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;vertical-align:top;padding:11px;border:1px solid var(--line);overflow-wrap:anywhere}th{background:#edf4f6}tr:nth-child(even){background:#fafcfd}
.notice{border-left:4px solid #d49a23;background:#fff5d9;padding:14px 18px;margin-bottom:20px}
@media(max-width:900px){.layout{display:block;padding:16px}nav{position:static;margin-bottom:20px}section{padding:24px}header{padding:24px}}
@media print{body{background:white;font-size:10pt}header{background:white;color:black;padding:0}header p{color:#333}nav{display:none}.layout{display:block;padding:0}section{border:0;padding:0;break-before:page}pre{background:#f0f0f0;color:black}pre code{white-space:pre-wrap}table{font-size:9pt}a{color:inherit}tr{break-inside:avoid}}
"""
    html_page = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>BOM Assistant maintenance handover</title><style>'+css+'</style><header><p>INTERNAL AND CONFIDENTIAL · 30 SEPTEMBER 2026</p><h1>BOM Assistant maintenance handover</h1><p>Application · PostgreSQL · Docker · Models · Recovery · Ownership</p></header><div class="layout"><nav>'+''.join(nav)+'</nav><main><div class="notice">This package contains live credentials and business records. Keep the entire folder in approved restricted team storage. Use the browser print command for a paper or PDF copy of this guide.</div>'+''.join(sections)+'</main></div></html>'
    (root / "START_HERE.html").write_text(html_page,encoding="utf-8")
    (root / "START_HERE.md").write_text(
        "# BOM Assistant internal handover\n\nOpen [START_HERE.html](START_HERE.html) for the complete guide, or "
        "[the Markdown chapters](source/docs/handover/README.md).\n\n"
        "This folder includes private configuration, original business files, tested application backups, "
        "Docker images and local model files. Keep it in approved restricted team storage.\n\n"
        "Verify after transfer: python source/infra/handover/verify_package.py .\n\n"
        "See [restore evidence](inventory/restore-verification.json), [validation](inventory/validation.json), "
        "and [package totals](inventory/package-summary.json). Complete ownership acceptance before disabling the old account.\n",
        encoding="utf-8")
    with tarfile.open(root / "PRIVATE/docker-images/images.tar") as archive:
        member = archive.extractfile("manifest.json")
        if member is None:
            raise RuntimeError("Docker image archive is missing its manifest")
        image_manifest = json.load(member)
        archived_ids = {entry["Config"].split("/")[-1].removesuffix(".json") for entry in image_manifest}
        # Docker's containerd store identifies an image by its OCI descriptor,
        # whereas legacy Docker archives use a config hash. Validate either form.
        if "index.json" in archive.getnames():
            index = json.load(archive.extractfile("index.json"))
            for entry in index["manifests"]:
                algorithm, value = entry["digest"].split(":", 1)
                data = archive.extractfile("blobs/" + algorithm + "/" + value).read()
                if algorithm != "sha256" or hashlib.sha256(data).hexdigest() != value:
                    raise RuntimeError("Corrupt OCI image descriptor")
                archived_ids.add(value)
    containers = json.loads((root/"inventory/containers.json").read_text())
    required_ids = {c["image_id"].removeprefix("sha256:") for c in containers}
    if not required_ids <= archived_ids:
        raise RuntimeError("A running Docker image is missing from the archive")
    write_json(root/"inventory/image-archive-verification.json", {
        "running_images_present":True,"manifest_entries":len(image_manifest),"running_image_count":len(required_ids)})
    existing = [p for p in root.rglob("*") if p.is_file() and p.name != "SHA256SUMS.json"
                and p != root/"inventory/package-summary.json"]
    write_json(root/"inventory/package-summary.json",{
        "created_utc":datetime.now(timezone.utc).isoformat(),
        "payload_bytes_before_summary_and_checksums":sum(p.stat().st_size for p in existing),
        "payload_files_before_summary_and_checksums":len(existing),
        "classification":"Confidential internal handover; not encrypted",
        "restored_tables":len(restore["restored"]),
        "restored_rows":sum(t["rows"] for t in restore["restored"].values()),
        "limitations":["Separate-host recovery and cold reboot not performed","Full-platform restore not rehearsed",
                       "Running Ollama API differs from copied default model store"]})
    print("Hashing all payload files including model blobs; this can take several minutes",flush=True)
    records = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path != root/"SHA256SUMS.json":
            records.append({"path":path.relative_to(root).as_posix(),"bytes":path.stat().st_size,"sha256":digest(path)})
            if len(records)%200 == 0:
                print(f"Hashed {len(records)} files",flush=True)
    hashes = {r["path"]:r for r in records}
    model_root = root/"PRIVATE/ollama/models"
    model_results = []
    for model in sorted((model_root/"manifests").rglob("*")):
        if not model.is_file():
            continue
        spec = json.loads(model.read_text())
        for blob in [spec["config"], *spec["layers"]]:
            key = "PRIVATE/ollama/models/blobs/" + blob["digest"].replace(":","-")
            saved = hashes.get(key)
            if not saved or saved["bytes"] != blob["size"] or saved["sha256"] != blob["digest"].split(":")[1]:
                raise RuntimeError("Missing or corrupt model blob: " + key)
        model_results.append({"manifest":model.relative_to(model_root).as_posix(),"blobs_verified":True})
    report = root/"inventory/model-archive-verification.json"
    write_json(report,model_results)
    records = [r for r in records if r["path"] != report.relative_to(root).as_posix()]
    records.append({"path":report.relative_to(root).as_posix(),"bytes":report.stat().st_size,"sha256":digest(report)})
    records.sort(key=lambda r:r["path"])
    write_json(root/"SHA256SUMS.json",{"algorithm":"sha256","created_utc":datetime.now(timezone.utc).isoformat(),"files":records})
    print(f"Finalized {len(records)} files, {sum(r['bytes'] for r in records):,} bytes; "
          f"{len(model_results)} model manifests verified.",flush=True)

if __name__ == "__main__":
    main()

