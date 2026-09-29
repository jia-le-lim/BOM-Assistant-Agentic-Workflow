"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useApi } from "@/lib/api";
import { can, useSession } from "@/lib/session";
import type { Batch, UploadSummary } from "@/lib/types";
import { Banner, BusyLabel, Progress } from "./ui";
import "./workspaces.css";

type SymbolName = "folder" | "file" | "review" | "plus" | "arrow" | "close" | "search" | "upload" | "check";
function Symbol({ name }: { name: SymbolName }) {
  const paths: Record<SymbolName, ReactNode> = {
    folder: <path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z" />,
    file: <><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Zm0 0v6h6M8 13h8M8 17h8" /><path d="M12 13v4" /></>,
    review: <><rect x="4" y="3" width="16" height="18" rx="2" /><path d="m8 9 1 1 2-2m2 1h3m-8 6 1 1 2-2m2 1h3" /></>,
    plus: <path d="M12 5v14M5 12h14" />,
    arrow: <path d="M5 12h14m-5-5 5 5-5 5" />,
    close: <path d="m6 6 12 12M6 18 18 6" />,
    search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 4 4" /></>,
    upload: <path d="M12 16V3m-4 4 4-4 4 4M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4" />,
    check: <path d="m5 12 4 4L19 6" />,
  };
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

function PreviewRows() {
  return <div className="ws-preview" aria-hidden="true"><div className="ws-preview-inner">
    {[0, 1, 2, 3].map((row) => <div className="ws-preview-row" key={row}>
      <span className="ws-preview-square" /><i /><i /><i /><span className="ws-preview-end" />
    </div>)}
  </div></div>;
}

const STEPS = [
  { icon: "folder" as const, title: "Create a workspace", text: "Give your review cycle a name. Keep each BOM review in its own space.", label: "01 · ORGANIZE" },
  { icon: "file" as const, title: "Upload your datasheet", text: "Drop in your BOM extract. We’ll prepare your parts for review.", label: "02 · CSV OR EXCEL" },
  { icon: "review" as const, title: "Review with confidence", text: "Run recommendations, review changes, and approve your next steps.", label: "03 · REVIEW & APPROVE" },
];

function SetupSteps() {
  return <div className="ws-steps">{STEPS.map((step) => <div className="ws-step" key={step.title}>
    <span className="ws-icon"><Symbol name={step.icon} /></span>
    <h3>{step.title}</h3><p>{step.text}</p><span className="ws-step-label">{step.label}</span>
  </div>)}</div>;
}

function Modal({ title, onClose, busy = false, children }: {
  title: string; onClose: () => void; busy?: boolean; children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const dialog = ref.current;
    dialog?.showModal();
    return () => { dialog?.close(); opener?.focus(); };
  }, []);
  return <dialog ref={ref} className="ws-dialog" aria-label={title}
    onCancel={(e) => { e.preventDefault(); if (!busy) onClose(); }}>
    <div className="ws-dialog-header"><span className="ws-icon"><Symbol name="folder" /></span>
      <button type="button" className="ws-icon-button" aria-label="Close dialog" disabled={busy} onClick={onClose}><Symbol name="close" /></button></div>
    <h2>{title}</h2>{children}
  </dialog>;
}

function SetupGuide({ onClose }: { onClose: () => void }) {
  return <Modal title="Your first BOM review" onClose={onClose}>
    <p className="ws-muted">Three steps from a new workspace to a review queue.</p>
    <ol className="ws-guide">{STEPS.map((step) => <li key={step.title}><strong>{step.title}</strong><p>{step.text}</p></li>)}</ol>
    <div className="ws-guide-note">Use a CSV, XLSX, or XLS file up to 100 MB. Your extract needs <code>item_id</code>, <code>max_qty</code>, <code>rop_qty</code>, <code>min_qty</code>, and <code>unitprice</code> columns. Choose the module that matches your extract.</div>
    <button type="button" className="ws-button ws-primary" onClick={onClose}>Got it<Symbol name="check" /></button>
  </Modal>;
}

function CreateWorkspace({ onClose }: { onClose: () => void }) {
  const { call } = useApi();
  const router = useRouter();
  const [name, setName] = useState("");
  const [module, setModule] = useState("TCB");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function create(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim() || busy) return;
    setBusy(true); setError(null);
    try {
      const workspace = await call<Batch>("batches", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label: name.trim(), module_filter: module }) });
      router.push(`/workspaces/${workspace.batch_id}`);
    } catch (e) { setError((e as Error).message); setBusy(false); }
  }
  return <Modal title="Create a new workspace" onClose={onClose} busy={busy}>
    <p className="ws-muted">A dedicated space for one BOM review cycle. Add your datasheet when you’re ready.</p>
    <form className="ws-create-form" onSubmit={create}>
      <label>Workspace name<input autoFocus className="field" placeholder="e.g. September 2026 · TCB review" value={name}
        onChange={(e) => setName(e.target.value)} required maxLength={120} disabled={busy} /></label>
      <label>Module<select className="field" value={module} onChange={(e) => setModule(e.target.value)} disabled={busy}>
        <option value="TCB">TCB</option><option value="Epoxy">Epoxy</option><option value="ALL">All modules</option>
      </select><span className="ws-field-hint">Only parts in this module will be included in the review.</span></label>
      {error && <div role="alert"><Banner kind="error">{error}</Banner></div>}
      <div className="ws-dialog-actions"><button type="button" className="ws-button" onClick={onClose} disabled={busy}>Cancel</button>
        <button className="ws-button ws-primary" disabled={busy || !name.trim()}><BusyLabel busy={busy} running="Creating…" idle="Create workspace" /><Symbol name="arrow" /></button></div>
    </form>
  </Modal>;
}

function workspaceStatus(batch: Batch) {
  if (batch.status === "draft") return "Awaiting datasheet";
  if (batch.status === "scored") return "Ready for review";
  if (batch.status === "uploading") return "Upload in progress";
  return "Ready to score";
}

export function WorkspacesHome() {
  const { call } = useApi();
  const { role, user } = useSession();
  const [batches, setBatches] = useState<Batch[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const load = useCallback(async (signal?: AbortSignal) => {
    try { const data = await call<Batch[]>("batches", { signal }); if (!signal?.aborted) { setBatches(data); setError(null); } }
    catch (e) { if (!signal?.aborted) setError((e as Error).message); }
  }, [call]);
  useEffect(() => {
    const controller = new AbortController();
    const frame = window.requestAnimationFrame(() => void load(controller.signal));
    return () => { window.cancelAnimationFrame(frame); controller.abort(); };
  }, [load]);
  useEffect(() => {
    const openFromHash = () => {
      if (["#new-workspace", "#upload-bom"].includes(window.location.hash)) {
        setCreateOpen(true);
        window.history.replaceState(null, "", window.location.pathname + window.location.search);
      }
    };
    const openWorkspace = () => setCreateOpen(true);
    openFromHash(); window.addEventListener("hashchange", openFromHash);
    window.addEventListener("bom:new-workspace", openWorkspace);
    return () => {
      window.removeEventListener("hashchange", openFromHash);
      window.removeEventListener("bom:new-workspace", openWorkspace);
    };
  }, []);
  const visible = batches?.filter((b) => (!query.trim() || `${b.label} ${b.module_filter}`.toLowerCase().includes(query.trim().toLowerCase()))
    && (filter === "all" || (filter === "draft" ? b.status === "draft" : b.status !== "draft")));
  const createButton = <button type="button" className="ws-button ws-primary" onClick={() => setCreateOpen(true)} disabled={!can.upload(role)}><Symbol name="plus" />Create workspace</button>;
  return <div className="ws-page page-wide">
    <header className="ws-heading"><div><div className="ws-eyebrow">YOUR REVIEW CYCLES</div><h1>Workspaces</h1>
      <p>Your workspaces are private to your account.</p></div>{Boolean(batches?.length) && createButton}</header>
    {error && <div role="alert"><Banner kind="error">Could not load workspaces. {error} <button type="button" className="ws-text-button" onClick={() => void load()}>Try again</button></Banner></div>}
    {batches === null ? !error && <div className="ws-empty ws-loading" role="status"><PreviewRows /><p>Loading your workspaces…</p></div>
      : batches.length === 0 ? <section className="ws-empty" aria-labelledby="ws-empty-title">
        <div className="ws-empty-content"><PreviewRows />
          <div className="ws-empty-copy"><h2 id="ws-empty-title">Your next BOM review starts here</h2>
            <p>Create a workspace for your review cycle, then add your datasheet.<br className="ws-desktop-break" /> Your parts, recommendations, and decisions — all in one place.</p></div>
          <SetupSteps /><div className="ws-empty-actions">{createButton}<button type="button" className="ws-text-button" onClick={() => setGuideOpen(true)}>Read the setup guide<Symbol name="arrow" /></button></div>
          <p className="ws-footnote">One workspace per review cycle. Start fresh, stay organized.</p>
        </div>
      </section> : <section className="ws-collection" aria-label="Review workspaces">
        <div className="ws-toolbar"><div className="ws-filters" aria-label="Filter workspaces">{[["all", "All workspaces"], ["draft", "Awaiting datasheet"], ["active", "In progress"]].map(([value, label]) =>
          <button type="button" key={value} aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}{value === "all" && <span>{batches.length}</span>}</button>)}</div>
          <label className="ws-search"><Symbol name="search" /><input aria-label="Search workspaces" placeholder="Search workspaces…" value={query} onChange={(e) => setQuery(e.target.value)} /></label></div>
        <div className="ws-grid">{visible?.map((batch) => <Link className="ws-workspace-card" key={batch.batch_id}
          href={batch.status === "scored" ? `/batches/${batch.batch_id}` : `/workspaces/${batch.batch_id}`}>
          <div className="ws-card-top"><span className="ws-icon"><Symbol name="folder" /></span><span className={`ws-status${batch.status === "scored" ? " is-ready" : ""}`}><span />{workspaceStatus(batch)}</span></div>
          <h2>{batch.label}</h2><p>{batch.module_filter === "ALL" ? "All modules" : batch.module_filter} <span>·</span> {batch.status === "draft" ? "No datasheet yet" : `${(batch.row_count ?? 0).toLocaleString()} parts`}</p>
          {batch.uploaded_by !== user && <p>Owner: {batch.uploaded_by || "Unknown"} · Read-only</p>}
          <div className="ws-card-bottom"><span>{batch.uploaded_by !== user ? "View workspace" : batch.status === "draft" ? "Add your datasheet" : "Continue review"}</span><Symbol name="arrow" /></div>
        </Link>)}</div>
        {visible?.length === 0 && <div className="ws-no-results"><h2>No workspaces found</h2><p>Try another name or module, or change the filter.</p><button type="button" className="ws-text-button" onClick={() => { setQuery(""); setFilter("all"); }}>Clear filters</button></div>}
        <p className="ws-list-note">Starting another review cycle? Create a new workspace to keep its datasheet and decisions together.</p>
      </section>}
    {createOpen && can.upload(role) && <CreateWorkspace onClose={() => setCreateOpen(false)} />}
    {guideOpen && <SetupGuide onClose={() => setGuideOpen(false)} />}
  </div>;
}

export function WorkspaceDetail({ id }: { id: string }) {
  const { call } = useApi();
  const { role, user } = useSession();
  const router = useRouter();
  const [batch, setBatch] = useState<Batch | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [match, setMatch] = useState("exact");
  const [scoreAfter, setScoreAfter] = useState(true);
  const [phase, setPhase] = useState<"upload" | "score" | null>(null);
  const [summary, setSummary] = useState<UploadSummary | null>(null);
  const [guideOpen, setGuideOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const result = await call<{ batch: Batch }>(`batches/${encodeURIComponent(id)}/summary`, { signal });
      if (!signal?.aborted) { setBatch(result.batch); setError(null); }
    } catch (e) { if (!signal?.aborted) setError((e as Error).message); }
  }, [call, id]);
  useEffect(() => {
    const controller = new AbortController();
    const frame = window.requestAnimationFrame(() => void load(controller.signal));
    return () => { window.cancelAnimationFrame(frame); controller.abort(); };
  }, [load]);
  const busy = phase !== null;
  const canEdit = can.upload(role) && batch?.uploaded_by === user;
  function choose(files: FileList | null) {
    if (busy || !canEdit || !files?.length) return;
    const next = files[0];
    if (files.length !== 1) { setError("Choose one datasheet for this review cycle."); return; }
    if (!/\.(csv|xlsx|xls)$/i.test(next.name)) { setError("Choose a CSV, XLSX, or XLS datasheet."); return; }
    if (next.size > 100 * 1024 * 1024) { setError("Your datasheet must be 100 MB or smaller."); return; }
    if (!next.size) { setError("This file is empty. Choose a datasheet with BOM data."); return; }
    setFile(next); setError(null);
  }
  async function start(event: React.FormEvent) {
    event.preventDefault();
    if (!batch || busy || !canEdit) return;
    setError(null);
    try {
      if (batch.status === "draft") {
        if (!file) return;
        setPhase("upload");
        const body = new FormData(); body.append("file", file); body.append("batch_id", id);
        body.append("label", batch.label); body.append("module_filter", batch.module_filter || "ALL"); body.append("module_match", match);
        const result = await call<UploadSummary>("upload-bom-file", { method: "POST", body });
        setSummary(result);
        setBatch({ ...batch, status: "loaded", source_filename: file.name, row_count: result.rows_loaded, quarantined_count: result.rows_quarantined });
        setFile(null);
        if (!scoreAfter) return;
      }
      setPhase("score");
      await call(`run-recommendation?batch_id=${id}`, { method: "POST" });
      router.push(`/batches/${id}`);
    } catch (e) { setError((e as Error).message); }
    finally { setPhase(null); }
  }
  return <div className="ws-page page-wide">
    <Link href="/" className="ws-back">← All workspaces</Link>
    {error && <div role="alert"><Banner kind="error">{error}{!batch && <button type="button" className="ws-text-button" onClick={() => void load()}>Try again</button>}</Banner></div>}
    {!batch ? !error && <div className="ws-empty ws-loading" role="status"><PreviewRows /><p>Loading workspace…</p></div> : <>
      <header className="ws-heading"><div><div className="ws-eyebrow">BOM REVIEW WORKSPACE</div><h1>{batch.label}</h1>
        <p>{batch.module_filter === "ALL" ? "All modules" : `${batch.module_filter} module`}<span className="ws-meta-divider">/</span>Review cycle #{batch.batch_id}</p></div><span className="ws-status"><span />{workspaceStatus(batch)}</span></header>
      <ol className="ws-progress" aria-label="Review setup progress">{["Create workspace", "Upload datasheet", "Review BOM"].map((step, i) => {
        const current = batch.status === "draft" ? 1 : 2;
        return <li key={step} className={i < current ? "is-complete" : i === current ? "is-current" : ""} aria-current={i === current ? "step" : undefined}><span>{i < current ? <Symbol name="check" /> : i + 1}</span>{step}</li>;
      })}</ol>
      <section className="ws-upload-panel">
        {batch.uploaded_by !== user && <Banner kind="info">Viewing {batch.uploaded_by || "another user"}&apos;s workspace. You have read-only access.</Banner>}
        {batch.status === "draft" ? <form onSubmit={start} className="ws-upload-form">
          <div className="ws-empty-copy"><span className="ws-icon ws-large-icon"><Symbol name="file" /></span><h2>Your workspace is ready</h2><p>Add the BOM datasheet for this review cycle to get started.</p></div>
          <div className={`ws-dropzone${dragging ? " is-dragging" : ""}${file ? " has-file" : ""}`}
            onDragOver={(e) => { e.preventDefault(); if (!busy) setDragging(true); }}
            onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragging(false); }}
            onDrop={(e) => { e.preventDefault(); setDragging(false); choose(e.dataTransfer.files); }}>
            <Symbol name={file ? "file" : "upload"} />
            <h3>{file ? file.name : "Drop your BOM datasheet here"}</h3>
            <p>{file ? `${(file.size / 1024).toLocaleString(undefined, { maximumFractionDigits: 1 })} KB · Ready to upload` : "CSV, XLSX, or XLS · Up to 100 MB"}</p>
            <button type="button" className="ws-button" onClick={() => fileRef.current?.click()} disabled={busy || !canEdit}>{file ? "Change file" : "Browse files"}</button>
            <input ref={fileRef} type="file" accept=".csv,.xlsx,.xls" className="sr-only" tabIndex={-1} aria-label="BOM datasheet" disabled={busy || !canEdit}
              onChange={(e) => { choose(e.target.files); e.target.value = ""; }} />
          </div>
          <div className="ws-upload-options"><label>Module matching<select className="field" value={match} onChange={(e) => setMatch(e.target.value)} disabled={busy || batch.module_filter === "ALL"}>
            <option value="exact">Exact module</option><option value="tag">Include multi-tag parts</option></select></label>
            <label className="ws-checkbox"><input type="checkbox" checked={scoreAfter} onChange={(e) => setScoreAfter(e.target.checked)} disabled={busy} />Run recommendations after upload</label></div>
          <button className="ws-button ws-primary ws-upload-submit" disabled={!file || busy || !canEdit}><BusyLabel busy={busy} running="Uploading datasheet…" idle={scoreAfter ? "Upload & start review" : "Upload datasheet"} /><Symbol name="arrow" /></button>
          {!canEdit && <p className="ws-muted">Your account does not have permission to upload to this workspace.</p>}
        </form> : batch.status === "uploading" ? <div className="ws-upload-form ws-empty-copy"><h2>Datasheet upload in progress</h2><p>This workspace is receiving a datasheet. Refresh to check whether it has finished.</p><button type="button" className="ws-button" onClick={() => void load()}>Refresh workspace</button></div>
          : <form className="ws-upload-form ws-loaded" onSubmit={start}><span className="ws-icon ws-large-icon"><Symbol name="check" /></span>
            <h2>Your datasheet is in place</h2><p className="ws-muted">{batch.source_filename}</p>
            <div className="ws-upload-counts"><span><strong>{batch.row_count.toLocaleString()}</strong>rows loaded</span><span><strong>{batch.quarantined_count.toLocaleString()}</strong>quarantined</span></div>
            {summary && Object.keys(summary.quarantine_reasons).length > 0 && <p className="ws-muted">{Object.entries(summary.quarantine_reasons).map(([reason, count]) => `${reason}: ${count}`).join(" · ")}</p>}
            <p className="ws-muted">{batch.status === "scored" ? "Your recommendations are ready. Continue to the review queue." : "Run the recommendation engine to prepare your review queue."}</p>
            {batch.status === "scored" ? <Link className="ws-button ws-primary" href={`/batches/${id}`}>Open review queue<Symbol name="arrow" /></Link>
              : <button className="ws-button ws-primary" disabled={busy || !canEdit}><BusyLabel busy={busy} running="Preparing recommendations…" idle="Run recommendations" /><Symbol name="arrow" /></button>}
          </form>}
        {busy && <div className="ws-upload-progress"><Progress label={phase === "score" ? "Running the engine over your BOM. Your datasheet has been saved." : "Uploading and checking your datasheet…"} /></div>}
        <div className="ws-upload-footer"><span>This datasheet belongs to this review cycle.</span><button type="button" className="ws-text-button" onClick={() => setGuideOpen(true)}>Datasheet requirements<Symbol name="arrow" /></button></div>
      </section>
    </>}
    {guideOpen && <SetupGuide onClose={() => setGuideOpen(false)} />}
  </div>;
}
