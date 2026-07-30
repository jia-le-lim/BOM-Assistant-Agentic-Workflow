"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import { fmtCompact, fmtUsd, useApi } from "@/lib/api";
import { can, useSession } from "@/lib/session";
import type { Recommendation, RecommendationPage, RunSummary, Status } from "@/lib/types";
import { ActionChip, Banner, ReasonCodes, RiskChip, Spinner, StatTile, StatusChip } from "@/components/ui";
import { ReasonCodeBars, WorkflowPipeline } from "@/components/WorkflowPipeline";

interface Summary {
  batch: { batch_id: number; label: string; status: string; row_count: number;
           quarantined_count: number; scored_rule_version: string | null };
  scored: number;
  statuses: Record<string, number>;
  risk_levels: Record<string, number>;
  actions: Record<string, number>;
  reason_codes: Record<string, number>;
  exposure_total_usd: number;
  exposure_pending_usd: number;
  export_ready_rows: number;
}

const PAGE = 25;

export default function BatchPage({ params }: { params: Promise<{ id: string }> }) {
  // Next.js 16: params is a Promise -- unwrap with React `use`.
  const { id } = use(params);
  const batchId = Number(id);
  const { call, raw } = useApi();
  const { role } = useSession();

  const [summary, setSummary] = useState<Summary | null>(null);
  const [page, setPage] = useState<RecommendationPage | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [status, setStatus] = useState<Status | "">("pending_review");
  const [risk, setRisk] = useState("");
  const [action, setAction] = useState("");
  const [reason, setReason] = useState("");
  const [minExp, setMinExp] = useState("");
  const [offset, setOffset] = useState(0);

  const loadSummary = useCallback(async () => {
    try { setSummary(await call<Summary>(`batches/${batchId}/summary`)); }
    catch (e) { setErr((e as Error).message); }
  }, [call, batchId]);

  const loadPage = useCallback(async () => {
    const q = new URLSearchParams({ batch_id: String(batchId), limit: String(PAGE), offset: String(offset) });
    if (status) q.set("status", status);
    if (risk) q.set("risk_level", risk);
    if (action) q.set("action", action);
    if (reason) q.set("reason_code", reason);
    if (minExp) q.set("min_exposure", minExp);
    try {
      setPage(await call<RecommendationPage>(`recommendations?${q}`));
      setErr(null);
    } catch (e) { setErr((e as Error).message); setPage(null); }
  }, [call, batchId, status, risk, action, reason, minExp, offset]);

  useEffect(() => { loadSummary(); }, [loadSummary]);
  useEffect(() => { loadPage(); }, [loadPage]);
  useEffect(() => { setOffset(0); }, [status, risk, action, reason, minExp]);

  async function runEngine() {
    setBusy(true); setErr(null); setNote(null);
    try {
      const s = await call<RunSummary>(`run-recommendation?batch_id=${batchId}`, { method: "POST" });
      setNote(`Scored ${s.rows_scored.toLocaleString()} rows with ${s.rule_version} — ` +
              `${s.review_required_Y.toLocaleString()} need review, ` +
              `${s.review_required_N.toLocaleString()} auto-clearable.`);
      await Promise.all([loadSummary(), loadPage()]);
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  /** csv = the update rows only; xlsx = the whole workbook, cells already filled. */
  async function download(format: "csv" | "xlsx") {
    setErr(null);
    const path = format === "xlsx" ? "export/wings.xlsx" : "export/wings";
    const res = await raw(`${path}?batch_id=${batchId}`);
    if (!res.ok) {
      setErr(`Export failed: ${(await res.json()).detail}`);
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `wings_update_batch${batchId}.${format}`; a.click();
    URL.revokeObjectURL(url);

    const pending = res.headers.get("x-pending-review");
    const senior = res.headers.get("x-awaiting-senior");
    setNote(format === "xlsx"
      ? `Workbook written: ${res.headers.get("x-rows-updated")} rows carry new `
        + `values, ${res.headers.get("x-rows-acknowledged")} reviewed with no `
        + `change. ${pending} pending review and ${senior} awaiting senior `
        + `approval were left blank.`
      : `Exported ${res.headers.get("x-rows-exported")} approved rows. `
        + `${pending} still pending review, ${senior} awaiting senior approval — excluded.`);
  }

  const statuses = (summary?.statuses ?? {}) as Record<Status, number>;
  const isScored = summary?.batch.status === "scored";

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link href="/" className="text-xs" style={{ color: "var(--text-muted)" }}>← Batches</Link>
          <h1 className="text-xl font-semibold mt-1">
            Batch #{batchId} {summary?.batch.label && `— ${summary.batch.label}`}
          </h1>
          <p className="text-sm mt-1" style={{ color: "var(--text-secondary)" }}>
            {summary
              ? `${summary.batch.row_count?.toLocaleString()} rows ingested · ` +
                `${summary.batch.quarantined_count} quarantined · ` +
                `${isScored ? `scored with ${summary.batch.scored_rule_version}` : "not yet scored"}`
              : "…"}
          </p>
        </div>
        <div className="flex gap-2">
          <button className="btn btn-primary" onClick={runEngine}
                  disabled={busy || !can.upload(role)}>
            {busy ? "Scoring…" : isScored ? "Re-run engine" : "Run engine"}
          </button>
          <button className="btn btn-primary" onClick={() => download("xlsx")}
                  disabled={!isScored || !can.export(role)}
                  title="The monthly workbook with approved values already in their cells">
            Export workbook
          </button>
          <button className="btn" onClick={() => download("csv")}
                  disabled={!isScored || !can.export(role)}
                  title="Just the changed rows, as CSV">
            Export update rows (CSV)
          </button>
        </div>
      </div>

      {err && <Banner kind="error">{err}</Banner>}
      {note && <Banner kind="success">{note}</Banner>}

      {!isScored && !err && (
        <Banner kind="info">
          This batch has not been scored yet. Run the engine to generate recommendations.
        </Banner>
      )}

      {summary && isScored && (
        <>
          <div className="grid gap-4 grid-cols-2 lg:grid-cols-4">
            <StatTile label="Scored rows" value={summary.scored} />
            <StatTile label="Needs a human"
                      value={(statuses.pending_review ?? 0) + (statuses.awaiting_senior ?? 0)}
                      sub="pending + awaiting senior" accent="var(--warning)" />
            <StatTile label="Exposure at stake" value={fmtCompact(summary.exposure_total_usd)}
                      sub={`${fmtCompact(summary.exposure_pending_usd)} still unreviewed`} />
            <StatTile label="Ready for WINGS" value={summary.export_ready_rows}
                      sub="approved & changed"
                      accent={summary.export_ready_rows ? "var(--success-text)" : undefined} />
          </div>

          <div className="grid gap-5 lg:grid-cols-2">
            <WorkflowPipeline counts={statuses} exported={summary.export_ready_rows} />
            <ReasonCodeBars codes={summary.reason_codes} />
          </div>
        </>
      )}

      <div className="card p-5">
        <div className="flex flex-wrap items-end gap-3 mb-4">
          <h2 className="text-sm font-semibold mr-auto">Review queue</h2>
          <label className="flex flex-col gap-1 text-xs">
            <span style={{ color: "var(--text-secondary)" }}>Status</span>
            <select className="field" value={status}
                    onChange={(e) => setStatus(e.target.value as Status | "")}>
              <option value="">All</option>
              <option value="pending_review">Pending review</option>
              <option value="awaiting_senior">Awaiting senior</option>
              <option value="reviewed">Reviewed</option>
              <option value="auto_cleared">Auto-cleared</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span style={{ color: "var(--text-secondary)" }}>Risk</span>
            <select className="field" value={risk} onChange={(e) => setRisk(e.target.value)}>
              <option value="">Any</option><option>High</option>
              <option>Medium</option><option>Low</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span style={{ color: "var(--text-secondary)" }}>Action</span>
            <select className="field" value={action} onChange={(e) => setAction(e.target.value)}>
              <option value="">Any</option><option>Increase</option>
              <option>Maintain</option><option>Decrease</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span style={{ color: "var(--text-secondary)" }}>Reason code</span>
            <input className="field" value={reason} placeholder="e.g. ZERO_RECOMMEND"
                   onChange={(e) => setReason(e.target.value)} />
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span style={{ color: "var(--text-secondary)" }}>Min exposure $</span>
            <input className="field w-28" type="number" value={minExp} placeholder="0"
                   onChange={(e) => setMinExp(e.target.value)} />
          </label>
        </div>

        <p className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>
          Sorted by exposure, highest first — the top 100 items carry ~76% of the value.
          Work this order.
        </p>

        {page === null ? <Spinner /> : page.items.length === 0 ? (
          <p className="text-sm py-6" style={{ color: "var(--text-muted)" }}>
            No rows match these filters.
          </p>
        ) : (
          <>
            <div className="scroll-x">
              <table className="w-full text-sm min-w-[900px]">
                <thead>
                  <tr>
                    <th>Item</th><th className="text-right">Exposure</th><th>Status</th>
                    <th>Risk</th><th>Action</th><th className="text-right">Max</th>
                    <th>Why</th><th></th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((r: Recommendation) => (
                    <tr key={r.item_id}>
                      <td className="font-mono text-xs">{r.item_id}</td>
                      <td className="text-right tnum">{fmtUsd(r.exposure_usd)}</td>
                      <td><StatusChip status={r.status} /></td>
                      <td><RiskChip level={r.risk_level} /></td>
                      <td><ActionChip action={r.action} /></td>
                      <td className="text-right tnum">{r.new_max}</td>
                      <td className="max-w-[300px]"><ReasonCodes codes={r.reason_code} /></td>
                      <td className="text-right">
                        <Link className="btn text-xs"
                              href={`/batches/${batchId}/items/${r.item_id}`}>
                          Review
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex items-center justify-between mt-4 text-xs">
              <span style={{ color: "var(--text-muted)" }}>
                {offset + 1}–{Math.min(offset + PAGE, page.total)} of {page.total.toLocaleString()}
              </span>
              <div className="flex gap-2">
                <button className="btn text-xs" disabled={offset === 0}
                        onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
                <button className="btn text-xs" disabled={offset + PAGE >= page.total}
                        onClick={() => setOffset(offset + PAGE)}>Next</button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
