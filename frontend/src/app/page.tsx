"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { useApi } from "@/lib/api";
import { can, useSession } from "@/lib/session";
import type { Batch, UploadSummary } from "@/lib/types";
import { Banner, BusyLabel, Progress, StatTile, TableSkeleton } from "@/components/ui";

export default function BatchesPage() {
  const { call } = useApi();
  const { role } = useSession();
  const [batches, setBatches] = useState<Batch[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Upload-and-score is two sequential round trips over a whole extract. Naming
  // the phase you are actually in is the difference between a slow page and a
  // page that looks broken.
  const [phase, setPhase] = useState<"upload" | "score" | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [label, setLabel] = useState("Jan26");
  const [moduleFilter, setModuleFilter] = useState("TCB");
  const [moduleMatch, setModuleMatch] = useState("exact");
  const [scoreAfter, setScoreAfter] = useState(true);

  const load = useCallback(async () => {
    try {
      setBatches(await call<Batch[]>("batches"));
      setErr(null);
    } catch (e) { setErr((e as Error).message); setBatches([]); }
  }, [call]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => { void load(); });
    return () => window.cancelAnimationFrame(frame);
  }, [load]);

  async function upload(e: React.FormEvent) {
    e.preventDefault();
    const f = fileRef.current?.files?.[0];
    if (!f) return;
    setBusy(true); setPhase("upload"); setErr(null); setNote(null);
    try {
      const fd = new FormData();
      fd.append("file", f);
      fd.append("label", label);
      fd.append("module_filter", moduleFilter);
      fd.append("module_match", moduleMatch);
      const s = await call<UploadSummary>("upload-bom-file", { method: "POST", body: fd });
      const reasons = Object.entries(s.quarantine_reasons)
        .map(([k, v]) => `${k}: ${v}`).join(", ") || "none";
      let msg = `Batch ${s.batch_id} — ${s.rows_loaded.toLocaleString()} rows loaded, ` +
               `${s.rows_quarantined} quarantined (${reasons}).`;
      if (scoreAfter) {
        setPhase("score");
        const run = await call<{ rows_scored: number; review_required_Y: number; review_required_N: number }>(
          `run-recommendation?batch_id=${s.batch_id}`, { method: "POST" });
        msg += ` Scored ${run.rows_scored.toLocaleString()} rows — ` +
               `${run.review_required_Y.toLocaleString()} need review, ` +
               `${run.review_required_N.toLocaleString()} auto-cleared. Open the batch to triage.`;
      }
      setNote(msg);
      if (fileRef.current) fileRef.current.value = "";
      await load();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); setPhase(null); }
  }

  const scored = batches?.filter((b) => b.status === "scored") ?? [];
  const totalRows = batches?.reduce((a, b) => a + (b.row_count ?? 0), 0) ?? 0;
  const totalQuar = batches?.reduce((a, b) => a + (b.quarantined_count ?? 0), 0) ?? 0;

  return (
    <div className="page-wide flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold">Review batches</h1>
        <p className="text-sm mt-1" style={{ color: "var(--text-secondary)" }}>
          Upload a monthly BOM extract, score it with the rule engine, then work the
          review queue. The engine calculates — the engineer approves.
        </p>
      </div>

      {err && <Banner kind="error">{err}</Banner>}
      {note && <Banner kind="success">{note}</Banner>}

      <div className="grid gap-4 grid-cols-2 lg:grid-cols-4">
        <StatTile label="Batches" value={batches?.length ?? 0} />
        <StatTile label="Scored" value={scored.length} sub="ready for review" />
        <StatTile label="Rows ingested" value={totalRows} />
        <StatTile label="Quarantined" value={totalQuar}
                  sub="corrupt rows, never scored"
                  accent={totalQuar ? "var(--critical)" : undefined} />
      </div>

      <form onSubmit={upload} className="card p-5 flex flex-col gap-3">
        <h2 className="text-sm font-semibold">Upload BOM extract</h2>
        <div className="flex flex-wrap gap-3 items-end">
          <label className="flex flex-col gap-1 text-xs">
            <span style={{ color: "var(--text-secondary)" }}>File</span>
            <input ref={fileRef} type="file" accept=".csv,.xlsx,.xls" required
                   className="field text-xs" disabled={!can.upload(role)} />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span style={{ color: "var(--text-secondary)" }}>Label</span>
            <input value={label} onChange={(e) => setLabel(e.target.value)}
                   className="field" required disabled={!can.upload(role)} />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span style={{ color: "var(--text-secondary)" }}>Module</span>
            <select value={moduleFilter} onChange={(e) => setModuleFilter(e.target.value)}
                    className="field" disabled={!can.upload(role)}>
              <option value="TCB">TCB (MVP scope)</option>
              <option value="Epoxy">Epoxy</option>
              <option value="ALL">All modules</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span style={{ color: "var(--text-secondary)" }}>Match</span>
            <select value={moduleMatch} onChange={(e) => setModuleMatch(e.target.value)}
                    className="field" disabled={!can.upload(role) || moduleFilter === "ALL"}
                    title="Exact = single-module cell; Multi-tag = row tagged with this module among others">
              <option value="exact">Exact</option>
              <option value="tag">Multi-tag</option>
            </select>
          </label>
          <button className="btn btn-primary" disabled={busy || !can.upload(role)}>
            <BusyLabel busy={busy}
                       running={phase === "score" ? "Scoring rows…" : "Uploading…"}
                       idle={scoreAfter ? "Upload & score" : "Upload & ingest"} />
          </button>
          <label className="flex items-center gap-2 text-xs" style={{ color: "var(--text-secondary)" }}>
            <input type="checkbox" checked={scoreAfter}
                   onChange={(e) => setScoreAfter(e.target.checked)}
                   disabled={!can.upload(role)} />
            Run engine now
          </label>
        </div>
        {busy && (
          <Progress label={phase === "score"
            ? "Scoring the batch with the statistical engine — this runs over every ingested row."
            : "Parsing, normalizing and quarantining the extract…"} />
        )}
        {!can.upload(role) && (
          <p className="text-xs" style={{ color: "var(--text-muted)" }}>
            Role <code>{role}</code> cannot upload. Switch to engineer, senior, admin or it.
          </p>
        )}
      </form>

      <div className="card p-5">
        <h2 className="text-sm font-semibold mb-3">Batches</h2>
        {batches === null ? (
          <TableSkeleton rows={4} cols={6} label="Loading batches" />
        ) : batches.length === 0 ? (
          <p className="text-sm py-4" style={{ color: "var(--text-muted)" }}>
            No batches yet — upload a CSV above to begin.
          </p>
        ) : (
          <div className="scroll-x">
            <table className="w-full text-sm min-w-[780px]">
              <thead>
                <tr>
                  <th>Batch</th><th>Module</th><th className="text-right">Rows</th>
                  <th className="text-right">Quarantined</th><th>Status</th>
                  <th>Rule version</th><th>Uploaded</th><th></th>
                </tr>
              </thead>
              <tbody>
                {batches.map((b) => (
                  <tr key={b.batch_id}>
                    <td className="font-medium">#{b.batch_id} {b.label}</td>
                    <td style={{ color: "var(--text-secondary)" }}>{b.module_filter}</td>
                    <td className="text-right tnum">{b.row_count?.toLocaleString()}</td>
                    <td className="text-right tnum"
                        style={{ color: b.quarantined_count ? "var(--critical)" : "var(--text-muted)" }}>
                      {b.quarantined_count}
                    </td>
                    <td>
                      <span className="text-xs whitespace-nowrap">
                        <span aria-hidden style={{ color: b.status === "scored" ? "var(--good)" : "var(--warning)" }}>
                          {b.status === "scored" ? "✓ " : "◷ "}
                        </span>
                        {b.status}
                      </span>
                    </td>
                    <td className="text-xs" style={{ color: "var(--text-muted)" }}>
                      {b.scored_rule_version ?? "—"}
                    </td>
                    <td className="text-xs whitespace-nowrap" style={{ color: "var(--text-muted)" }}>
                      {b.uploaded_at} · {b.uploaded_by}
                    </td>
                    <td className="text-right">
                      <Link className="btn text-xs" href={`/batches/${b.batch_id}`}>Open</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
