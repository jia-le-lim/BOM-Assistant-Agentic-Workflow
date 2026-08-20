"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { fmtCompact, fmtUsd, useApi } from "@/lib/api";
import { can, useSession } from "@/lib/session";
import type {
  BatchSummary, BulkReviewResult, Recommendation, RecommendationPage, RuleConfig, RunSummary,
  SimilarityRunSummary, Status, TriagePage, TriageRunSummary,
} from "@/lib/types";
import {
  AgreementChip, Banner, ConsumableChip, ReasonCodes, RiskChip,
  Spinner, StatTile, StatusChip,
} from "@/components/ui";
import { WorkflowPipeline } from "@/components/WorkflowPipeline";
import { AgentTriage, PriorityCallout, TriageLanes } from "@/components/TriageLanes";

const PAGE = 25;

/** Composite key: an item can sit in more than one stockroom. */
const keyOf = (r: { item_id: string; stockroom_id: string }) =>
  `${r.item_id}::${r.stockroom_id}`;

export default function BatchPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const batchId = Number(id);
  const { call, raw } = useApi();
  const { role } = useSession();
  const canReview = can.review(role);
  const router = useRouter();
  const searchParams = useSearchParams();

  const [summary, setSummary] = useState<BatchSummary | null>(null);
  const [page, setPage] = useState<RecommendationPage | null>(null);
  const [triage, setTriage] = useState<TriagePage | null>(null);
  const [ruleConfig, setRuleConfig] = useState<RuleConfig | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [triageBudget, setTriageBudget] = useState("2000");
  const [triageRefresh, setTriageRefresh] = useState(false);
  const [similarityRefresh, setSimilarityRefresh] = useState(false);

  // Initialize filter state from URL query parameters
  const [status, setStatus] = useState<Status | "">(
    (searchParams.get("status") as Status) || "pending_review"
  );
  const [risk, setRisk] = useState(searchParams.get("risk_level") || "");
  const [action, setAction] = useState(searchParams.get("action") || "");
  const [consumable, setConsumable] = useState(searchParams.get("consumable") || "");
  const [agreement, setAgreement] = useState(searchParams.get("agreement") || "");
  const [minExp, setMinExp] = useState(searchParams.get("min_exposure") || "");
  const [offset, setOffset] = useState(Number(searchParams.get("offset")) || 0);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  // Helper to update URL with current filters
  const updateUrl = useCallback((
    newStatus?: Status | "",
    newRisk?: string,
    newAction?: string,
    newConsumable?: string,
    newAgreement?: string,
    newMinExp?: string,
    newOffset?: number,
  ) => {
    const params = new URLSearchParams();
    const s = newStatus ?? status;
    const r = newRisk ?? risk;
    const a = newAction ?? action;
    const c = newConsumable ?? consumable;
    const ag = newAgreement ?? agreement;
    const m = newMinExp ?? minExp;
    const o = newOffset ?? offset;

    if (s) params.set("status", s);
    if (r) params.set("risk_level", r);
    if (a) params.set("action", a);
    if (c) params.set("consumable", c);
    if (ag) params.set("agreement", ag);
    if (m) params.set("min_exposure", m);
    if (o > 0) params.set("offset", String(o));

    router.push(`/batches/${batchId}?${params.toString()}`);
  }, [batchId, router, status, risk, action, consumable, agreement, minExp, offset]);

  const loadSummary = useCallback(async () => {
    try { setSummary(await call<BatchSummary>(`batches/${batchId}/summary`)); }
    catch (e) { setErr((e as Error).message); }
  }, [call, batchId]);

  const loadPage = useCallback(async () => {
    const q = new URLSearchParams({ batch_id: String(batchId), limit: String(PAGE), offset: String(offset) });
    if (status) q.set("status", status);
    if (risk) q.set("risk_level", risk);
    if (action) q.set("action", action);
    if (consumable) q.set("consumable", consumable);
    if (agreement) q.set("agreement", agreement);
    if (minExp) q.set("min_exposure", minExp);
    try {
      setPage(await call<RecommendationPage>(`recommendations?${q}`));
      setErr(null);
    } catch (e) { setErr((e as Error).message); setPage(null); }
  }, [call, batchId, status, risk, action, consumable, agreement, minExp, offset]);

  const loadTriage = useCallback(async () => {
    if (!canReview) { setTriage(null); return; }
    try { setTriage(await call<TriagePage>(`triage/${batchId}`)); }
    catch (e) { setErr((e as Error).message); }
  }, [call, batchId, canReview]);

  const loadRuleConfig = useCallback(async () => {
    try { setRuleConfig(await call<RuleConfig>("config/rules")); }
    catch (e) { setErr((e as Error).message); }
  }, [call]);

  // Create wrapper functions for filter setters that update both state and URL
  const handleStatusChange = useCallback((value: string) => {
    const v = value as Status | "";
    setStatus(v);
    setOffset(0);
    setSelected(new Set());
    updateUrl(v, risk, action, consumable, agreement, minExp, 0);
  }, [updateUrl, risk, action, consumable, agreement, minExp]);

  const handleRiskChange = useCallback((v: string) => {
    setRisk(v);
    setOffset(0);
    setSelected(new Set());
    updateUrl(status, v, action, consumable, agreement, minExp, 0);
  }, [updateUrl, status, action, consumable, agreement, minExp]);

  const handleActionChange = useCallback((v: string) => {
    setAction(v);
    setOffset(0);
    setSelected(new Set());
    updateUrl(status, risk, v, consumable, agreement, minExp, 0);
  }, [updateUrl, status, risk, consumable, agreement, minExp]);

  const handleConsumableChange = useCallback((v: string) => {
    setConsumable(v);
    setOffset(0);
    setSelected(new Set());
    updateUrl(status, risk, action, v, agreement, minExp, 0);
  }, [updateUrl, status, risk, action, agreement, minExp]);

  const handleAgreementChange = useCallback((v: string) => {
    setAgreement(v);
    setOffset(0);
    setSelected(new Set());
    updateUrl(status, risk, action, consumable, v, minExp, 0);
  }, [updateUrl, status, risk, action, consumable, minExp]);

  const handleMinExpChange = useCallback((v: string) => {
    setMinExp(v);
    setOffset(0);
    setSelected(new Set());
    updateUrl(status, risk, action, consumable, agreement, v, 0);
  }, [updateUrl, status, risk, action, consumable, agreement]);

  const handleOffsetChange = useCallback((v: number) => {
    setOffset(v);
    updateUrl(status, risk, action, consumable, agreement, minExp, v);
  }, [updateUrl, status, risk, action, consumable, agreement, minExp]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => { void loadSummary(); });
    return () => window.cancelAnimationFrame(frame);
  }, [loadSummary]);
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => { void loadPage(); });
    return () => window.cancelAnimationFrame(frame);
  }, [loadPage]);
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => { void loadTriage(); });
    return () => window.cancelAnimationFrame(frame);
  }, [loadTriage]);
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => { void loadRuleConfig(); });
    return () => window.cancelAnimationFrame(frame);
  }, [loadRuleConfig]);

  const isScored = summary?.batch.status === "scored";
  const statuses = (summary?.statuses ?? {}) as Record<Status, number>;
  const needsHuman = (statuses.pending_review ?? 0) + (statuses.awaiting_senior ?? 0);

  const refresh = useCallback(async () => {
    await Promise.all([loadSummary(), loadPage(), loadTriage()]);
  }, [loadSummary, loadPage, loadTriage]);

  async function runEngine() {
    setBusy(true); setErr(null); setNote(null);
    try {
      const s = await call<RunSummary>(`run-recommendation?batch_id=${batchId}`, { method: "POST" });
      setNote(`Scored ${s.rows_scored.toLocaleString()} rows with ${s.rule_version} — ` +
              `${s.review_required_Y.toLocaleString()} need review, ` +
              `${s.review_required_N.toLocaleString()} auto-cleared.`);
      await refresh();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  async function runSimilarity() {
    setBusy(true); setErr(null); setNote(null);
    try {
      const s = await call<SimilarityRunSummary>("similarity/run", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ batch_id: batchId, refresh: similarityRefresh }),
      });
      setNote(s.neighbour_pool === 0
        ? "No reviewed history yet — every row is flagged as having no reliable "
          + "analogue. Run again once this month's reviews are recorded."
        : `Matched ${s.scored.toLocaleString()} rows against `
          + `${s.neighbour_pool.toLocaleString()} reviewed peers — `
          + `${s.outliers.toLocaleString()} unusual, `
          + `${s.diverging.toLocaleString()} diverging from peer median. `
          + `${s.categorised.toLocaleString()} of ${s.scored.toLocaleString()} `
          + `had a part category.`);
      setSimilarityRefresh(false);
      await refresh();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  async function runTriage() {
    setBusy(true); setErr(null); setNote(null);
    try {
      const s = await call<TriageRunSummary>("triage/run", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          batch_id: batchId,
          llm_call_budget: Number(triageBudget),
          refresh: triageRefresh,
        }),
      });
      setNote(`Triaged ${s.triaged.toLocaleString()} rows with ${s.llm_calls_used.toLocaleString()} ` +
              `model calls${s.budget_exhausted ? "; budget reached, run again to resume" : ""}.`);
      setTriageRefresh(false);
      await loadTriage();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  function summarise(r: BulkReviewResult): string {
    const bits = [`${r.reviewed.toLocaleString()} accepted`];
    if (r.awaiting_senior) bits.push(`${r.awaiting_senior} sent for senior approval`);
    if (r.skipped) bits.push(`${r.skipped} already decided`);
    if (r.failed.length) bits.push(`${r.failed.length} failed`);
    return bits.join(", ");
  }

  async function acceptLane(cons: string) {
    setBusy(true); setErr(null); setNote(null);
    try {
      const r = await call<BulkReviewResult>("review/bulk", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          batch_id: batchId, decision: "accept",
          justification: "bulk: safe agreements in lane",
          filters: { consumable: cons, min_confidence: 0.8, exclude_high_risk: true },
        }),
      });
      setNote(`${cons} lane — ${summarise(r)}.`);
      setSelected(new Set());
      await refresh();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  async function acceptGuardedTriage() {
    setBusy(true); setErr(null); setNote(null);
    try {
      const r = await call<BulkReviewResult>("review/bulk", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          batch_id: batchId, decision: "accept",
          justification: "bulk: human-confirmed guarded triage candidates",
          filters: { triage_preselect: true },
        }),
      });
      setNote(`Guarded triage selection — ${summarise(r)}.`);
      setSelected(new Set());
      await refresh();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  async function bulkSelected(decision: "accept" | "reject") {
    if (!selected.size) return;
    setBusy(true); setErr(null); setNote(null);
    try {
      const items = [...selected].map((k) => {
        const [item_id, stockroom_id] = k.split("::");
        return { item_id, stockroom_id };
      });
      const r = await call<BulkReviewResult>("review/bulk", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          batch_id: batchId, decision,
          justification: `bulk: ${decision} from queue`, items,
        }),
      });
      setNote(`${decision === "accept" ? "Accepted" : "Rejected"} selection — ${summarise(r)}.`);
      setSelected(new Set());
      await refresh();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  async function quickReview(r: Recommendation, decision: "accept" | "reject") {
    setBusy(true); setErr(null); setNote(null);
    try {
      const res = await call<{ status: string; requires_senior_approval: boolean }>(
        `review/${r.item_id}?batch_id=${batchId}&stockroom_id=${encodeURIComponent(r.stockroom_id)}`,
        { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ decision, justification: "quick review" }) },
      );
      setNote(res.requires_senior_approval
        ? `${r.item_id} recorded — awaiting senior approval.`
        : `${r.item_id} ${decision}ed.`);
      await refresh();
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  async function download(format: "csv" | "xlsx") {
    setErr(null);
    const path = format === "xlsx" ? "export/wings.xlsx" : "export/wings";
    const res = await raw(`${path}?batch_id=${batchId}`);
    if (!res.ok) { setErr(`Export failed: ${(await res.json()).detail}`); return; }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `wings_update_batch${batchId}.${format}`; a.click();
    URL.revokeObjectURL(url);
    setNote(`Export ready — ${res.headers.get("x-rows-updated") ?? res.headers.get("x-rows-exported") ?? ""} rows.`);
  }

  const pageKeys = useMemo(() => (page?.items ?? []).map(keyOf), [page]);
  const allSelected = pageKeys.length > 0 && pageKeys.every((k) => selected.has(k));

  function toggle(k: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k); else next.add(k);
      return next;
    });
  }
  function toggleAll() {
    setSelected((prev) => {
      const next = new Set(prev);
      if (allSelected) pageKeys.forEach((k) => next.delete(k));
      else pageKeys.forEach((k) => next.add(k));
      return next;
    });
  }

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
          <button className="btn btn-primary" onClick={runEngine} disabled={busy || !can.upload(role)}>
            {busy ? "Working…" : isScored ? "Re-run engine" : "Run engine"}
          </button>
          <button className="btn btn-primary" onClick={() => download("xlsx")}
                  disabled={!isScored || !can.export(role)}
                  title="The monthly workbook with approved values already in their cells">
            Export workbook
          </button>
          <button className="btn" onClick={() => download("csv")}
                  disabled={!isScored || !can.export(role)} title="Just the changed rows, as CSV">
            Export CSV
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
            <StatTile label="Needs a human" value={needsHuman}
                      sub="pending + awaiting senior" accent="var(--warning)" />
            <StatTile label="Safe to bulk-accept" value={summary.bulk_acceptable}
                      sub="high-confidence agreements"
                      accent={summary.bulk_acceptable ? "var(--success-text)" : undefined} />
            <StatTile label="Exposure at stake" value={fmtCompact(summary.exposure_total_usd)}
                      sub={`${fmtCompact(summary.exposure_pending_usd)} still unreviewed`} />
            <StatTile label="Ready for WINGS" value={summary.export_ready_rows}
                      sub="approved & changed"
                      accent={summary.export_ready_rows ? "var(--success-text)" : undefined} />
          </div>

          <PriorityCallout summary={summary} />

          {canReview && (
            <div className="card p-5">
              <div className="flex flex-wrap items-end gap-3">
                <div className="mr-auto">
                  <h2 className="text-sm font-semibold">Run peer similarity</h2>
                  <p className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>
                    Matches each scored row against previously reviewed parts. Advisory
                    evidence only — it never changes Min/ROP/Max or a risk level.
                  </p>
                </div>
                <label className="flex items-center gap-2 text-xs pb-2">
                  <input type="checkbox" checked={similarityRefresh}
                         onChange={(e) => setSimilarityRefresh(e.target.checked)} />
                  Rebuild existing results
                </label>
                <button className="btn btn-primary" onClick={runSimilarity}
                        disabled={busy}>
                  {busy ? "Working…" : "Run similarity"}
                </button>
              </div>
            </div>
          )}

          {canReview && (
            <div className="card p-5">
              <div className="flex flex-wrap items-end gap-3">
                <div className="mr-auto">
                  <h2 className="text-sm font-semibold">Run advisory triage</h2>
                  <p className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>
                    Resumes missing rows by default. The model call budget is capped at 2,000 per run.
                  </p>
                </div>
                <label className="flex flex-col gap-1 text-xs">
                  <span style={{ color: "var(--text-secondary)" }}>Model call budget</span>
                  <input className="field w-28 tnum" type="number" min={3} max={2000}
                         value={triageBudget} onChange={(e) => setTriageBudget(e.target.value)} />
                </label>
                <label className="flex items-center gap-2 text-xs pb-2">
                  <input type="checkbox" checked={triageRefresh}
                         onChange={(e) => setTriageRefresh(e.target.checked)} />
                  Rebuild existing results
                </label>
                <button className="btn btn-primary" onClick={runTriage}
                        disabled={busy || !triageBudget}>
                  {busy ? "Working…" : triage?.total ? "Resume triage" : "Run triage"}
                </button>
              </div>
            </div>
          )}

          {canReview && triage && (
            <AgentTriage batchId={batchId} triage={triage} busy={busy}
                         guardedAssist={Boolean(ruleConfig?.config.triage_guarded_assist_enabled)}
                         onGuardedAccept={acceptGuardedTriage} />
          )}

          <div className="grid gap-5 lg:grid-cols-2">
            <TriageLanes summary={summary} busy={busy} canReview={canReview}
                         onAcceptLane={acceptLane} />
            <WorkflowPipeline counts={statuses} exported={summary.export_ready_rows} />
          </div>
        </>
      )}

      <div className="card p-5">
        <div className="flex flex-wrap items-end gap-3 mb-4">
          <h2 className="text-sm font-semibold mr-auto">Review queue</h2>
          <Filter label="Status" value={status} set={handleStatusChange}
                  opts={[["", "All"], ["pending_review", "Pending"], ["awaiting_senior", "Awaiting senior"],
                         ["reviewed", "Reviewed"], ["auto_cleared", "Auto-cleared"]]} />
          <Filter label="Demand" value={consumable} set={handleConsumableChange}
                  opts={[["", "Any"], ["constant", "Constant"], ["sporadic", "Sporadic"],
                         ["dying", "Dying"], ["none", "Dormant"]]} />
          <Filter label="Agreement" value={agreement} set={handleAgreementChange}
                  opts={[["", "Any"], ["match", "Matches"], ["diverge", "Diverges"], ["none", "No benchmark"]]} />
          <Filter label="Risk" value={risk} set={handleRiskChange}
                  opts={[["", "Any"], ["High", "High"], ["Medium", "Medium"], ["Low", "Low"]]} />
          <Filter label="Action" value={action} set={handleActionChange}
                  opts={[["", "Any"], ["Increase", "Increase"], ["Maintain", "Maintain"], ["Decrease", "Decrease"]]} />
          <label className="flex flex-col gap-1 text-xs">
            <span style={{ color: "var(--text-secondary)" }}>Min exposure $</span>
            <input className="field w-28" type="number" value={minExp} placeholder="0"
                   onChange={(e) => handleMinExpChange(e.target.value)} />
          </label>
        </div>

        <p className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>
          Sorted by exposure, highest first. Tick rows to accept or reject in bulk, or use the
          inline actions. Overrides still open the item detail.
        </p>

        {selected.size > 0 && (
          <div className="flex items-center gap-3 mb-3 p-2 rounded"
               style={{ background: "var(--seq-soft)" }}>
            <span className="text-sm">{selected.size} selected</span>
            <button className="btn btn-primary text-xs" disabled={busy || !canReview}
                    onClick={() => bulkSelected("accept")}>Accept selected</button>
            <button className="btn text-xs" disabled={busy || !canReview}
                    onClick={() => bulkSelected("reject")}>Reject selected</button>
            <button className="btn text-xs" onClick={() => setSelected(new Set())}>Clear</button>
          </div>
        )}

        {page === null ? <Spinner /> : page.items.length === 0 ? (
          <p className="text-sm py-6" style={{ color: "var(--text-muted)" }}>
            No rows match these filters.
          </p>
        ) : (
          <>
            <div className="scroll-x">
              <table className="w-full text-sm min-w-[1000px]">
                <thead>
                  <tr>
                    <th className="w-8">
                      <input type="checkbox" checked={allSelected} onChange={toggleAll}
                             aria-label="Select all on page" />
                    </th>
                    <th>Item</th><th>Demand</th><th className="text-right">Exposure</th>
                    <th>Status</th><th>Risk</th><th>Agreement</th>
                    <th className="text-right">Max</th><th>Why</th><th></th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((r: Recommendation) => {
                    const k = keyOf(r);
                    const pending = r.status === "pending_review";
                    return (
                      <tr key={k}>
                        <td>
                          <input type="checkbox" checked={selected.has(k)}
                                 disabled={!pending}
                                 onChange={() => toggle(k)}
                                 aria-label={`Select ${r.item_id}`} />
                        </td>
                        <td className="font-mono text-xs">{r.item_id}</td>
                        <td><ConsumableChip value={r.consumable} /></td>
                        <td className="text-right tnum">{fmtUsd(r.exposure_usd)}</td>
                        <td><StatusChip status={r.status} /></td>
                        <td><RiskChip level={r.risk_level} /></td>
                        <td><AgreementChip value={r.agreement} /></td>
                        <td className="text-right tnum">{r.new_max}</td>
                        <td className="max-w-[240px]"><ReasonCodes codes={r.reason_code} /></td>
                        <td>
                          <div className="flex gap-1.5 justify-end">
                            {pending && canReview && (
                              <>
                                <button className="btn text-xs" disabled={busy}
                                        onClick={() => quickReview(r, "accept")}>Accept</button>
                                <button className="btn text-xs" disabled={busy}
                                        onClick={() => quickReview(r, "reject")}>Reject</button>
                              </>
                            )}
                            <Link className="btn text-xs"
                                  href={`/batches/${batchId}/items/${r.item_id}`}>Open</Link>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="flex items-center justify-between mt-4 text-xs">
              <span style={{ color: "var(--text-muted)" }}>
                {offset + 1}–{Math.min(offset + PAGE, page.total)} of {page.total.toLocaleString()}
              </span>
              <div className="flex gap-2">
                <button className="btn text-xs" disabled={offset === 0}
                        onClick={() => handleOffsetChange(Math.max(0, offset - PAGE))}>Previous</button>
                <button className="btn text-xs" disabled={offset + PAGE >= page.total}
                        onClick={() => handleOffsetChange(offset + PAGE)}>Next</button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function Filter({ label, value, set, opts }: {
  label: string; value: string; set: (v: string) => void; opts: [string, string][];
}) {
  return (
    <label className="flex flex-col gap-1 text-xs">
      <span style={{ color: "var(--text-secondary)" }}>{label}</span>
      <select className="field" value={value} onChange={(e) => set(e.target.value)}>
        {opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  );
}
